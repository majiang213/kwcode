// Package middleware provides HTTP middleware for a Go web server.
// Bugs:
// 1. Logger middleware logs AFTER the handler runs, not before (wrong order)
// 2. Auth middleware checks header "X-Auth-Token" but tests send "Authorization"
// 3. Recovery middleware re-panics instead of recovering
package middleware

import (
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Handler is a function that handles an HTTP request.
type Handler func(w http.ResponseWriter, r *http.Request)

// Middleware wraps a Handler.
type Middleware func(Handler) Handler

// Chain applies middlewares in order: first middleware is outermost.
func Chain(h Handler, middlewares ...Middleware) Handler {
	for i := len(middlewares) - 1; i >= 0; i-- {
		h = middlewares[i](h)
	}
	return h
}

// ResponseRecorder captures the status code written by a handler.
type ResponseRecorder struct {
	http.ResponseWriter
	StatusCode int
	Body       strings.Builder
}

func NewResponseRecorder(w http.ResponseWriter) *ResponseRecorder {
	return &ResponseRecorder{ResponseWriter: w, StatusCode: http.StatusOK}
}

func (r *ResponseRecorder) WriteHeader(code int) {
	r.StatusCode = code
	r.ResponseWriter.WriteHeader(code)
}

func (r *ResponseRecorder) Write(b []byte) (int, error) {
	r.Body.Write(b)
	return r.ResponseWriter.Write(b)
}

// LogEntry records a single request log.
type LogEntry struct {
	Method     string
	Path       string
	StatusCode int
	Duration   time.Duration
	LoggedAt   string // "before" or "after" handler
}

// Logger returns a middleware that logs requests.
// It must log BEFORE the handler runs (LoggedAt = "before").
func Logger(log *[]LogEntry) Middleware {
	return func(next Handler) Handler {
		return func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			rr := NewResponseRecorder(w)

			// Bug: calls handler first, then logs (should log before)
			next(rr, r)

			*log = append(*log, LogEntry{
				Method:     r.Method,
				Path:       r.URL.Path,
				StatusCode: rr.StatusCode,
				Duration:   time.Since(start),
				LoggedAt:   "before",
			})
		}
	}
}

// Auth returns a middleware that checks for a valid token.
// Expects header: "Authorization: Bearer <token>"
func Auth(validTokens map[string]string) Middleware {
	return func(next Handler) Handler {
		return func(w http.ResponseWriter, r *http.Request) {
			// Bug: reads "X-Auth-Token" instead of "Authorization"
			header := r.Header.Get("X-Auth-Token")
			if !strings.HasPrefix(header, "Bearer ") {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			token := strings.TrimPrefix(header, "Bearer ")
			user, ok := validTokens[token]
			if !ok {
				http.Error(w, "forbidden", http.StatusForbidden)
				return
			}
			r.Header.Set("X-User", user)
			next(w, r)
		}
	}
}

// Recovery returns a middleware that recovers from panics.
func Recovery(onPanic func(interface{})) Middleware {
	return func(next Handler) Handler {
		return func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if rec := recover(); rec != nil {
					if onPanic != nil {
						onPanic(rec)
					}
					// Bug: re-panics instead of writing 500 response
					panic(rec)
				}
			}()
			next(w, r)
		}
	}
}

// CORS returns a middleware that adds CORS headers.
func CORS(allowedOrigins []string) Middleware {
	return func(next Handler) Handler {
		return func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			for _, allowed := range allowedOrigins {
				if allowed == "*" || allowed == origin {
					w.Header().Set("Access-Control-Allow-Origin", origin)
					w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
					w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
					break
				}
			}
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next(w, r)
		}
	}
}

// RateLimit returns a simple in-memory rate limiter middleware.
func RateLimit(maxPerSecond int, counter map[string]int) Middleware {
	return func(next Handler) Handler {
		return func(w http.ResponseWriter, r *http.Request) {
			ip := r.RemoteAddr
			counter[ip]++
			if counter[ip] > maxPerSecond {
				http.Error(w, fmt.Sprintf("rate limit exceeded"), http.StatusTooManyRequests)
				return
			}
			next(w, r)
		}
	}
}
