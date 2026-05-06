package workerpool

import (
	"errors"
	"sort"
	"sync/atomic"
	"testing"
	"time"
)

func makeJob(id int, val int) Job {
	return Job{
		ID:      id,
		Payload: val,
		Process: func(p interface{}) (interface{}, error) {
			return p.(int) * 2, nil
		},
	}
}

func makeFailJob(id int) Job {
	return Job{
		ID:      id,
		Payload: nil,
		Process: func(p interface{}) (interface{}, error) {
			return nil, errors.New("job failed")
		},
	}
}

func TestPoolProcessesAllJobs(t *testing.T) {
	p := New(3, 20)
	n := 10
	for i := 0; i < n; i++ {
		if err := p.Submit(makeJob(i, i+1)); err != nil {
			t.Fatalf("Submit failed: %v", err)
		}
	}
	p.Stop()
	results := Collect(p.Results())
	if len(results) != n {
		t.Errorf("expected %d results, got %d", n, len(results))
	}
}

func TestPoolResultValues(t *testing.T) {
	p := New(2, 10)
	p.Submit(makeJob(1, 5))
	p.Submit(makeJob(2, 10))
	p.Stop()
	results := CollectN(p.Results(), 2)
	for _, r := range results {
		if r.Err != nil {
			t.Errorf("unexpected error: %v", r.Err)
		}
	}
	// Find result for job 1
	for _, r := range results {
		if r.JobID == 1 && r.Value.(int) != 10 {
			t.Errorf("job 1: expected value 10, got %v", r.Value)
		}
	}
}

func TestPoolHandlesErrors(t *testing.T) {
	p := New(2, 10)
	p.Submit(makeJob(1, 5))
	p.Submit(makeFailJob(2))
	p.Stop()
	results := Collect(p.Results())
	errCount := CountErrors(results)
	if errCount != 1 {
		t.Errorf("expected 1 error, got %d", errCount)
	}
}

func TestSubmitAfterStopReturnsError(t *testing.T) {
	p := New(2, 10)
	p.Stop()
	err := p.Submit(makeJob(1, 1))
	if !errors.Is(err, ErrPoolStopped) {
		t.Errorf("expected ErrPoolStopped, got %v", err)
	}
}

func TestPoolWaitsForAllWorkers(t *testing.T) {
	var processed int64
	p := New(4, 20)
	n := 20
	for i := 0; i < n; i++ {
		id := i
		p.Submit(Job{
			ID:      id,
			Payload: id,
			Process: func(v interface{}) (interface{}, error) {
				time.Sleep(time.Millisecond)
				atomic.AddInt64(&processed, 1)
				return v, nil
			},
		})
	}
	p.Stop()
	// After Stop(), all jobs must be processed
	if atomic.LoadInt64(&processed) != int64(n) {
		t.Errorf("expected %d processed, got %d", n, atomic.LoadInt64(&processed))
	}
}

func TestCollectOrderedByJobID(t *testing.T) {
	p := New(4, 20)
	n := 10
	for i := n - 1; i >= 0; i-- {
		p.Submit(makeJob(i, i))
	}
	p.Stop()
	results := Collect(p.Results())
	// Sort by JobID
	sort.Slice(results, func(i, j int) bool {
		return results[i].JobID < results[j].JobID
	})
	for i, r := range results {
		if r.JobID != i {
			t.Errorf("position %d: expected JobID %d, got %d", i, i, r.JobID)
		}
	}
}

func TestPoolConcurrency(t *testing.T) {
	var concurrent int64
	var maxConcurrent int64
	var mu int64
	p := New(3, 50)
	for i := 0; i < 30; i++ {
		p.Submit(Job{
			ID:      i,
			Payload: i,
			Process: func(v interface{}) (interface{}, error) {
				cur := atomic.AddInt64(&concurrent, 1)
				for {
					old := atomic.LoadInt64(&maxConcurrent)
					if cur <= old || atomic.CompareAndSwapInt64(&maxConcurrent, old, cur) {
						break
					}
				}
				time.Sleep(time.Millisecond)
				atomic.AddInt64(&concurrent, -1)
				_ = mu
				return v, nil
			},
		})
	}
	p.Stop()
	if maxConcurrent > 3 {
		t.Errorf("max concurrent workers exceeded pool size: got %d", maxConcurrent)
	}
}
