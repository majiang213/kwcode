package pipeline

import (
	"context"
	"sort"
	"testing"
)

func collect[T any](ch <-chan T) []T {
	var out []T
	for v := range ch {
		out = append(out, v)
	}
	return out
}

func TestMap(t *testing.T) {
	ctx := context.Background()
	in := Generate(ctx, []int{1, 2, 3, 4, 5})
	doubled := Map(func(x int) int { return x * 2 })(ctx, in)
	result := collect(doubled)
	expected := []int{2, 4, 6, 8, 10}
	if len(result) != len(expected) {
		t.Fatalf("expected %v, got %v", expected, result)
	}
	for i, v := range expected {
		if result[i] != v {
			t.Errorf("index %d: expected %d, got %d", i, v, result[i])
		}
	}
}

func TestFilter(t *testing.T) {
	ctx := context.Background()
	in := Generate(ctx, []int{1, 2, 3, 4, 5, 6})
	evens := Filter(func(x int) bool { return x%2 == 0 })(ctx, in)
	result := collect(evens)
	for _, v := range result {
		if v%2 != 0 {
			t.Errorf("expected only even numbers, got %d", v)
		}
	}
	if len(result) != 3 {
		t.Errorf("expected 3 even numbers, got %d: %v", len(result), result)
	}
}

func TestReduce(t *testing.T) {
	ctx := context.Background()
	in := Generate(ctx, []int{1, 2, 3, 4, 5})
	sum := Reduce(func(acc, x int) int { return acc + x }, 0)(ctx, in)
	if sum != 15 {
		t.Errorf("expected sum=15, got %d", sum)
	}
}

func TestBatch(t *testing.T) {
	ctx := context.Background()
	in := Generate(ctx, []int{1, 2, 3, 4, 5})
	batched := Batch[int](2)(ctx, in)
	result := collect(batched)
	if len(result) != 3 {
		t.Fatalf("expected 3 batches, got %d", len(result))
	}
	if len(result[0]) != 2 || len(result[1]) != 2 || len(result[2]) != 1 {
		t.Errorf("unexpected batch sizes: %v", result)
	}
}

func TestMerge(t *testing.T) {
	ctx := context.Background()
	in1 := Generate(ctx, []int{1, 3, 5})
	in2 := Generate(ctx, []int{2, 4, 6})
	merged := Merge(ctx, in1, in2)
	result := collect(merged)
	sort.Ints(result)
	if len(result) != 6 {
		t.Fatalf("expected 6 items, got %d", len(result))
	}
	for i, v := range []int{1, 2, 3, 4, 5, 6} {
		if result[i] != v {
			t.Errorf("index %d: expected %d, got %d", i, v, result[i])
		}
	}
}

func TestFanOut(t *testing.T) {
	ctx := context.Background()
	in := Generate(ctx, []int{1, 2, 3})
	outs := FanOut(ctx, in, 3)
	results := make([][]int, 3)
	var done = make(chan struct{}, 3)
	for i, ch := range outs {
		go func(idx int, c <-chan int) {
			results[idx] = collect(c)
			done <- struct{}{}
		}(i, ch)
	}
	for i := 0; i < 3; i++ {
		<-done
	}
	// Each output should receive all 3 items
	for i, r := range results {
		if len(r) != 3 {
			t.Errorf("output %d: expected 3 items, got %d: %v", i, len(r), r)
		}
	}
}

func TestTee(t *testing.T) {
	ctx := context.Background()
	in := Generate(ctx, []int{1, 2, 3})
	out1, out2 := Tee(ctx, in)
	r1 := collect(out1)
	r2 := collect(out2)
	if len(r1) != 3 || len(r2) != 3 {
		t.Errorf("expected 3 items in each output, got %d and %d", len(r1), len(r2))
	}
}

func TestPipelineComposition(t *testing.T) {
	ctx := context.Background()
	in := Generate(ctx, []int{1, 2, 3, 4, 5, 6, 7, 8, 9, 10})
	// Filter evens, double them, batch by 2
	evens := Filter(func(x int) bool { return x%2 == 0 })(ctx, in)
	doubled := Map(func(x int) int { return x * 2 })(ctx, evens)
	batched := Batch[int](2)(ctx, doubled)
	result := collect(batched)
	// evens: 2,4,6,8,10 -> doubled: 4,8,12,16,20 -> batched: [4,8],[12,16],[20]
	if len(result) != 3 {
		t.Fatalf("expected 3 batches, got %d: %v", len(result), result)
	}
	if result[0][0] != 4 || result[0][1] != 8 {
		t.Errorf("first batch: expected [4,8], got %v", result[0])
	}
}

func TestContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	in := make(chan int, 100)
	for i := 0; i < 100; i++ {
		in <- i
	}
	close(in)
	cancel() // cancel immediately
	mapped := Map(func(x int) int { return x })(ctx, in)
	// Should not block
	collect(mapped)
}
