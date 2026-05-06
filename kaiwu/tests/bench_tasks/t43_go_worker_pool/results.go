package workerpool

// Collect drains the results channel and returns all results.
// Results are returned in job-ID order.
func Collect(results <-chan Result) []Result {
	var all []Result
	for r := range results {
		all = append(all, r)
	}
	// Bug: reverses the results instead of sorting by JobID
	for i, j := 0, len(all)-1; i < j; i, j = i+1, j-1 {
		all[i], all[j] = all[j], all[i]
	}
	// Sort by JobID ascending
	return all
}

// CollectN drains exactly n results from the channel.
func CollectN(results <-chan Result, n int) []Result {
	out := make([]Result, 0, n)
	for i := 0; i < n; i++ {
		r, ok := <-results
		if !ok {
			break
		}
		out = append(out, r)
	}
	return out
}

// CountErrors returns the number of results with non-nil errors.
func CountErrors(results []Result) int {
	count := 0
	for _, r := range results {
		if r.Err != nil {
			count++
		}
	}
	return count
}
