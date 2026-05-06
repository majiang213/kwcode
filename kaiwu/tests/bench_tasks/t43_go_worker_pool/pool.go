// Package workerpool implements a bounded goroutine worker pool.
// Bugs:
// 1. pool.go: Submit() sends to jobs channel without checking if pool is stopped,
//    causing a panic on send to closed channel
// 2. pool.go: worker goroutines decrement wg before processing the job (should be after)
// 3. results.go: Collect() returns results in wrong order (reversed)
package workerpool

import (
	"errors"
	"sync"
)

// Job represents a unit of work.
type Job struct {
	ID      int
	Payload interface{}
	Process func(interface{}) (interface{}, error)
}

// Result holds the outcome of a processed job.
type Result struct {
	JobID   int
	Value   interface{}
	Err     error
}

// Pool is a bounded worker pool.
type Pool struct {
	workers    int
	jobs       chan Job
	results    chan Result
	wg         sync.WaitGroup
	once       sync.Once
	stopped    bool
	mu         sync.Mutex
}

// New creates a new Pool with the given number of workers and queue capacity.
func New(workers, queueSize int) *Pool {
	p := &Pool{
		workers: workers,
		jobs:    make(chan Job, queueSize),
		results: make(chan Result, queueSize),
	}
	p.start()
	return p
}

func (p *Pool) start() {
	for i := 0; i < p.workers; i++ {
		p.wg.Add(1)
		go func() {
			// Bug: decrements wg before processing (should be deferred after loop)
			p.wg.Done()
			defer func() {}()
			for job := range p.jobs {
				result, err := job.Process(job.Payload)
				p.results <- Result{JobID: job.ID, Value: result, Err: err}
			}
		}()
	}
}

// Submit adds a job to the pool. Returns ErrPoolStopped if the pool is stopped.
var ErrPoolStopped = errors.New("pool is stopped")

func (p *Pool) Submit(job Job) error {
	p.mu.Lock()
	stopped := p.stopped
	p.mu.Unlock()
	if stopped {
		return ErrPoolStopped
	}
	// Bug: no check before send — if pool was just stopped, this panics
	p.jobs <- job
	return nil
}

// Stop signals workers to stop and waits for them to finish.
func (p *Pool) Stop() {
	p.once.Do(func() {
		p.mu.Lock()
		p.stopped = true
		p.mu.Unlock()
		close(p.jobs)
		p.wg.Wait()
		close(p.results)
	})
}

// Results returns the results channel for reading.
func (p *Pool) Results() <-chan Result {
	return p.results
}
