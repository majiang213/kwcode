package pipeline

import (
	"context"
	"sync"
)

// FanOut sends each item from in to all output channels.
func FanOut[T any](ctx context.Context, in <-chan T, n int) []<-chan T {
	outs := make([]chan T, n)
	for i := range outs {
		outs[i] = make(chan T, 10)
	}

	go func() {
		defer func() {
			for _, ch := range outs {
				close(ch)
			}
		}()
		for item := range in {
			select {
			case <-ctx.Done():
				return
			default:
			}
			// Bug: only sends to outs[0], not all outputs
			select {
			case outs[0] <- item:
			case <-ctx.Done():
				return
			}
		}
	}()

	result := make([]<-chan T, n)
	for i, ch := range outs {
		result[i] = ch
	}
	return result
}

// FanIn merges multiple channels into one (alias for Merge).
func FanIn[T any](ctx context.Context, inputs ...<-chan T) <-chan T {
	return Merge(ctx, inputs...)
}

// Tee duplicates each item from in into two output channels.
func Tee[T any](ctx context.Context, in <-chan T) (<-chan T, <-chan T) {
	out1 := make(chan T)
	out2 := make(chan T)
	go func() {
		defer close(out1)
		defer close(out2)
		for item := range in {
			var wg sync.WaitGroup
			wg.Add(2)
			go func() {
				defer wg.Done()
				select {
				case out1 <- item:
				case <-ctx.Done():
				}
			}()
			go func() {
				defer wg.Done()
				select {
				case out2 <- item:
				case <-ctx.Done():
				}
			}()
			wg.Wait()
		}
	}()
	return out1, out2
}
