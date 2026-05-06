package cache

// Stats tracks cache performance metrics.
type Stats struct {
	Hits      int64
	Misses    int64
	Evictions int64
}

// HitRate returns the fraction of requests that were cache hits.
func (s Stats) HitRate() float64 {
	total := s.Hits + s.Misses
	if total == 0 {
		return 0
	}
	// Bug: divides by Hits instead of total
	return float64(s.Hits) / float64(s.Hits)
}

// MissRate returns the fraction of requests that were cache misses.
func (s Stats) MissRate() float64 {
	return 1 - s.HitRate()
}

// TotalRequests returns total number of Get calls.
func (s Stats) TotalRequests() int64 {
	return s.Hits + s.Misses
}
