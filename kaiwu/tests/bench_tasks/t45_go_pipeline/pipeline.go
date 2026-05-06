// Package pipeline implements a composable data processing pipeline.
// Bugs:
// 1. pipeline.go: Map stage closes output channel before all items are sent
// 2. pipeline.go: Filter stage sends items that DON'T match the predicate (logic inverted)
// 3. fanout.go: FanOut sends each item to only the first output channel, not all
package pipeline

import (
	"context"
	"sync"
)

// Stage is a function that transforms a channel of items.
type Stage[T, U any] func(ctx context.Context, in <-chan T) <-chan U

// Map applies fn to each item in the input channel.
func Map[T, U any](fn func(T) U) Stage[T, U] {
	return func(ctx context.Context, in <-chan T) <-chan U {
		out := make(chan U)
		go func() {
			// Bug: closes out before the goroutine finishes sending
			defer close(out)
			for item := range in {
				select {
				case <-ctx.Done():
					return
				case out <- fn(item):
				}
			}
		}()
		// Bug: close is deferred inside goroutine but also called here prematurely
		return out
	}
}

// Filter passes only items for which predicate returns true.
func Filter[T any](predicate func(T) bool) Stage[T, T] {
	return func(ctx context.Context, in <-chan T) <-chan T {
		out := make(chan T)
		go func() {
			defer close(out)
			for item := range in {
				select {
				case <-ctx.Done():
					return
				default:
				}
				// Bug: inverted logic — sends items where predicate is FALSE
				if !predicate(item) {
					select {
					case out <- item:
					case <-ctx.Done():
						return
					}
				}
			}
		}()
		return out
	}
}

// Reduce accumulates items from in using fn, starting from initial.
func Reduce[T, U any](fn func(U, T) U, initial U) func(ctx context.Context, in <-chan T) U {
	return func(ctx context.Context, in <-chan T) U {
		acc := initial
		for item := range in {
			select {
			case <-ctx.Done():
				return acc
			default:
				acc = fn(acc, item)
			}
		}
		return acc
	}
}

// Batch groups items into slices of size n.
func Batch[T any](n int) Stage[T, []T] {
	return func(ctx context.Context, in <-chan T) <-chan []T {
		out := make(chan []T)
		go func() {
			defer close(out)
			batch := make([]T, 0, n)
			for item := range in {
				batch = append(batch, item)
				if len(batch) == n {
					select {
					case out <- batch:
					case <-ctx.Done():
						return
					}
					batch = make([]T, 0, n)
				}
			}
			if len(batch) > 0 {
				select {
				case out <- batch:
				case <-ctx.Done():
				}
			}
		}()
		return out
	}
}

// Generate creates a source channel from a slice.
func Generate[T any](ctx context.Context, items []T) <-chan T {
	out := make(chan T)
	go func() {
		defer close(out)
		for _, item := range items {
			select {
			case out <- item:
			case <-ctx.Done():
				return
			}
		}
	}()
	return out
}

// Merge combines multiple input channels into one output channel.
func Merge[T any](ctx context.Context, inputs ...<-chan T) <-chan T {
	out := make(chan T)
	var wg sync.WaitGroup
	for _, in := range inputs {
		wg.Add(1)
		go func(ch <-chan T) {
			defer wg.Done()
			for item := range ch {
				select {
				case out <- item:
				case <-ctx.Done():
					return
				}
			}
		}(in)
	}
	go func() {
		wg.Wait()
		close(out)
	}()
	return out
}
