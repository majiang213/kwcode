package cache

import (
	"testing"
	"time"
)

func TestSetAndGet(t *testing.T) {
	c := New(10, 0)
	c.Set("k", "v", 0)
	val, ok := c.Get("k")
	if !ok {
		t.Fatal("expected key to exist")
	}
	if val != "v" {
		t.Errorf("expected 'v', got %v", val)
	}
}

func TestMissReturnsNil(t *testing.T) {
	c := New(10, 0)
	_, ok := c.Get("missing")
	if ok {
		t.Error("expected miss for missing key")
	}
}

func TestTTLExpiry(t *testing.T) {
	c := New(10, 0)
	c.Set("k", "v", 50*time.Millisecond)
	time.Sleep(100 * time.Millisecond)
	_, ok := c.Get("k")
	if ok {
		t.Error("expected key to be expired")
	}
}

func TestTTLNotExpiredYet(t *testing.T) {
	c := New(10, 0)
	c.Set("k", "v", 500*time.Millisecond)
	_, ok := c.Get("k")
	if !ok {
		t.Error("expected key to still be valid")
	}
}

func TestLRUEviction(t *testing.T) {
	c := New(3, 0)
	c.Set("a", 1, 0)
	c.Set("b", 2, 0)
	c.Set("c", 3, 0)
	// Access "a" to make it recently used
	c.Get("a")
	// Adding "d" should evict "b" (LRU)
	c.Set("d", 4, 0)
	_, ok := c.Get("b")
	if ok {
		t.Error("expected 'b' to be evicted (LRU)")
	}
	_, ok = c.Get("a")
	if !ok {
		t.Error("expected 'a' to still exist (was recently accessed)")
	}
}

func TestLRUEvictionOrder(t *testing.T) {
	c := New(2, 0)
	c.Set("a", 1, 0)
	c.Set("b", 2, 0)
	// "a" is LRU; adding "c" should evict "a"
	c.Set("c", 3, 0)
	_, ok := c.Get("a")
	if ok {
		t.Error("expected 'a' to be evicted")
	}
	_, ok = c.Get("b")
	if !ok {
		t.Error("expected 'b' to still exist")
	}
}

func TestDelete(t *testing.T) {
	c := New(10, 0)
	c.Set("k", "v", 0)
	ok := c.Delete("k")
	if !ok {
		t.Error("expected Delete to return true")
	}
	_, ok = c.Get("k")
	if ok {
		t.Error("expected key to be deleted")
	}
}

func TestDeleteMissing(t *testing.T) {
	c := New(10, 0)
	ok := c.Delete("missing")
	if ok {
		t.Error("expected Delete to return false for missing key")
	}
}

func TestStatsHitRate(t *testing.T) {
	c := New(10, 0)
	c.Set("k", "v", 0)
	c.Get("k")  // hit
	c.Get("k")  // hit
	c.Get("x")  // miss
	c.Get("y")  // miss
	stats := c.Stats()
	if stats.Hits != 2 {
		t.Errorf("expected 2 hits, got %d", stats.Hits)
	}
	if stats.Misses != 2 {
		t.Errorf("expected 2 misses, got %d", stats.Misses)
	}
	rate := stats.HitRate()
	if rate < 0.49 || rate > 0.51 {
		t.Errorf("expected hit rate ~0.5, got %f", rate)
	}
}

func TestStatsEvictions(t *testing.T) {
	c := New(2, 0)
	c.Set("a", 1, 0)
	c.Set("b", 2, 0)
	c.Set("c", 3, 0) // evicts one
	stats := c.Stats()
	if stats.Evictions != 1 {
		t.Errorf("expected 1 eviction, got %d", stats.Evictions)
	}
}

func TestConcurrentAccess(t *testing.T) {
	c := New(100, 0)
	done := make(chan struct{})
	for i := 0; i < 10; i++ {
		go func(n int) {
			for j := 0; j < 100; j++ {
				key := string(rune('a' + n))
				c.Set(key, j, 0)
				c.Get(key)
			}
			done <- struct{}{}
		}(i)
	}
	for i := 0; i < 10; i++ {
		<-done
	}
}

func TestDefaultTTL(t *testing.T) {
	c := New(10, 50*time.Millisecond)
	c.Set("k", "v", 0) // uses default TTL
	time.Sleep(100 * time.Millisecond)
	_, ok := c.Get("k")
	if ok {
		t.Error("expected key to expire via default TTL")
	}
}
