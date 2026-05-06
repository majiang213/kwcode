package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestChain(t *testing.T) {
	order := []string{}
	mw1 := func(next Handler) Handler {
		return func(w http.ResponseWriter, r *http.Request) {
			order = append(order, "mw1-before")
			next(w, r)
			order = append(order, "mw1-after")
		}
	}
	mw2 := func(next Handler) Handler {
		return func(w http.ResponseWriter, r *http.Request) {
			order = append(order, "mw2-before")
			next(w, r)
			order = append(order, "mw2-after")
		}
	}
	handler := func(w http.ResponseWriter, r *http.Request) {
		order = append(order, "handler")
	}
	h := Chain(handler, mw1, mw2)
	req := httptest.NewRequest("GET", "/", nil)
	h(httptest.NewRecorder(), req)
	expected := []string{"mw1-before", "mw2-before", "handler", "mw2-after", "mw1-after"}
	if len(order) != len(expected) {
		t.Fatalf("expected %v, got %v", expected, order)
	}
	for i, v := range expected {
		if order[i] != v {
			t.Errorf("position %d: expected %q, got %q", i, v, order[i])
		}
	}
}

func TestLoggerLogsBeforeHandler(t *testing.T) {
	log := []LogEntry{}
	handlerCalled := false
	handler := func(w http.ResponseWriter, r *http.Request) {
		// When handler runs, the log entry should already exist
		if len(log) == 0 {
			t.Error("Logger must log BEFORE the handler runs")
		}
		handlerCalled = true
		w.WriteHeader(http.StatusOK)
	}
	h := Chain(handler, Logger(&log))
	req := httptest.NewRequest("GET", "/test", nil)
	h(httptest.NewRecorder(), req)
	if !handlerCalled {
		t.Error("handler was not called")
	}
	if len(log) != 1 {
		t.Fatalf("expected 1 log entry, got %d", len(log))
	}
	if log[0].Path != "/test" {
		t.Errorf("expected path /test, got %s", log[0].Path)
	}
	if log[0].LoggedAt != "before" {
		t.Errorf("expected LoggedAt=before, got %s", log[0].LoggedAt)
	}
}

func TestLoggerCapturesStatusCode(t *testing.T) {
	log := []LogEntry{}
	handler := func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}
	h := Chain(handler, Logger(&log))
	req := httptest.NewRequest("GET", "/missing", nil)
	h(httptest.NewRecorder(), req)
	if log[0].StatusCode != http.StatusNotFound {
		t.Errorf("expected 404, got %d", log[0].StatusCode)
	}
}

func TestAuthAllowsValidToken(t *testing.T) {
	tokens := map[string]string{"secret-token": "alice"}
	handler := func(w http.ResponseWriter, r *http.Request) {
		user := r.Header.Get("X-User")
		if user != "alice" {
			t.Errorf("expected X-User=alice, got %q", user)
		}
		w.WriteHeader(http.StatusOK)
	}
	h := Chain(handler, Auth(tokens))
	req := httptest.NewRequest("GET", "/", nil)
	req.Header.Set("Authorization", "Bearer secret-token")
	rr := httptest.NewRecorder()
	h(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rr.Code)
	}
}

func TestAuthRejectsNoToken(t *testing.T) {
	tokens := map[string]string{"secret-token": "alice"}
	handler := func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}
	h := Chain(handler, Auth(tokens))
	req := httptest.NewRequest("GET", "/", nil)
	rr := httptest.NewRecorder()
	h(rr, req)
	if rr.Code != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d", rr.Code)
	}
}

func TestAuthRejectsInvalidToken(t *testing.T) {
	tokens := map[string]string{"secret-token": "alice"}
	handler := func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}
	h := Chain(handler, Auth(tokens))
	req := httptest.NewRequest("GET", "/", nil)
	req.Header.Set("Authorization", "Bearer wrong-token")
	rr := httptest.NewRecorder()
	h(rr, req)
	if rr.Code != http.StatusForbidden {
		t.Errorf("expected 403, got %d", rr.Code)
	}
}

func TestRecoveryHandlesPanic(t *testing.T) {
	var recovered interface{}
	handler := func(w http.ResponseWriter, r *http.Request) {
		panic("something went wrong")
	}
	h := Chain(handler, Recovery(func(v interface{}) {
		recovered = v
	}))
	req := httptest.NewRequest("GET", "/", nil)
	rr := httptest.NewRecorder()
	// Should NOT panic
	func() {
		defer func() {
			if r := recover(); r != nil {
				t.Errorf("Recovery middleware should not re-panic, but got: %v", r)
			}
		}()
		h(rr, req)
	}()
	if rr.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", rr.Code)
	}
	if recovered != "something went wrong" {
		t.Errorf("expected recovered value, got %v", recovered)
	}
}

func TestCORSAddsHeaders(t *testing.T) {
	handler := func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}
	h := Chain(handler, CORS([]string{"https://example.com"}))
	req := httptest.NewRequest("GET", "/", nil)
	req.Header.Set("Origin", "https://example.com")
	rr := httptest.NewRecorder()
	h(rr, req)
	if rr.Header().Get("Access-Control-Allow-Origin") != "https://example.com" {
		t.Errorf("expected CORS header, got %q", rr.Header().Get("Access-Control-Allow-Origin"))
	}
}

func TestCORSWildcard(t *testing.T) {
	handler := func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}
	h := Chain(handler, CORS([]string{"*"}))
	req := httptest.NewRequest("GET", "/", nil)
	req.Header.Set("Origin", "https://any.com")
	rr := httptest.NewRecorder()
	h(rr, req)
	if rr.Header().Get("Access-Control-Allow-Origin") == "" {
		t.Error("expected CORS header for wildcard origin")
	}
}

func TestRateLimit(t *testing.T) {
	counter := map[string]int{}
	handler := func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}
	h := Chain(handler, RateLimit(2, counter))
	for i := 0; i < 3; i++ {
		req := httptest.NewRequest("GET", "/", nil)
		req.RemoteAddr = "1.2.3.4:1234"
		rr := httptest.NewRecorder()
		h(rr, req)
		if i < 2 && rr.Code != http.StatusOK {
			t.Errorf("request %d: expected 200, got %d", i, rr.Code)
		}
		if i == 2 && rr.Code != http.StatusTooManyRequests {
			t.Errorf("request %d: expected 429, got %d", i, rr.Code)
		}
	}
}

func TestMiddlewareChainWithAuth(t *testing.T) {
	log := []LogEntry{}
	tokens := map[string]string{"tok": "bob"}
	handler := func(w http.ResponseWriter, r *http.Request) {
		user := r.Header.Get("X-User")
		w.Write([]byte("hello " + user))
	}
	h := Chain(handler, Logger(&log), Auth(tokens))
	req := httptest.NewRequest("GET", "/secure", nil)
	req.Header.Set("Authorization", "Bearer tok")
	rr := httptest.NewRecorder()
	h(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "bob") {
		t.Errorf("expected body to contain 'bob', got %q", rr.Body.String())
	}
	if len(log) != 1 {
		t.Errorf("expected 1 log entry, got %d", len(log))
	}
}
