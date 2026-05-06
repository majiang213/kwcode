// Package cache implements a thread-safe LRU cache with TTL support.
// Bugs:
// 1. cache.go: Get() does not move accessed entry to front (breaks LRU order)
// 2. cache.go: evict() removes from front but LRU should be at back (list direction wrong)
// 3. stats.go: HitRate() divides by Hits instead of total requests
package cache

import (
	"container/list"
	"sync"
	"time"
)

type entry struct {
	key       string
	value     interface{}
	expiresAt time.Time
}

// Cache is a thread-safe LRU cache with optional TTL.
type Cache struct {
	mu       sync.Mutex
	cap      int
	items    map[string]*list.Element
	order    *list.List // front = most recently used
	stats    Stats
	defaultTTL time.Duration
}

// New creates a new Cache with the given capacity and default TTL.
// TTL of 0 means no expiry.
func New(capacity int, defaultTTL time.Duration) *Cache {
	return &Cache{
		cap:        capacity,
		items:      make(map[string]*list.Element),
		order:      list.New(),
		defaultTTL: defaultTTL,
	}
}

// Set stores a key-value pair. Uses defaultTTL if ttl is 0.
func (c *Cache) Set(key string, value interface{}, ttl time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if ttl == 0 {
		ttl = c.defaultTTL
	}

	var exp time.Time
	if ttl > 0 {
		exp = time.Now().Add(ttl)
	}

	if el, ok := c.items[key]; ok {
		c.order.MoveToFront(el)
		el.Value.(*entry).value = value
		el.Value.(*entry).expiresAt = exp
		return
	}

	el := c.order.PushFront(&entry{key: key, value: value, expiresAt: exp})
	c.items[key] = el

	if c.order.Len() > c.cap {
		c.evict()
	}
}

// Get retrieves a value by key. Returns (value, true) or (nil, false).
func (c *Cache) Get(key string) (interface{}, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()

	el, ok := c.items[key]
	if !ok {
		c.stats.Misses++
		return nil, false
	}

	e := el.Value.(*entry)
	if !e.expiresAt.IsZero() && time.Now().After(e.expiresAt) {
		c.removeElement(el)
		c.stats.Misses++
		return nil, false
	}

	// Bug: does not move to front on access (breaks LRU ordering)
	c.stats.Hits++
	return e.value, true
}

// Delete removes a key from the cache.
func (c *Cache) Delete(key string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	el, ok := c.items[key]
	if !ok {
		return false
	}
	c.removeElement(el)
	return true
}

func (c *Cache) removeElement(el *list.Element) {
	c.order.Remove(el)
	delete(c.items, el.Value.(*entry).key)
}

// evict removes the least recently used entry.
// LRU is at the back of the list (front = MRU).
func (c *Cache) evict() {
	// Bug: removes from front (MRU) instead of back (LRU)
	el := c.order.Front()
	if el != nil {
		c.removeElement(el)
		c.stats.Evictions++
	}
}

// Len returns the number of items in the cache.
func (c *Cache) Len() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.order.Len()
}

// Stats returns a copy of the cache statistics.
func (c *Cache) Stats() Stats {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.stats
}
