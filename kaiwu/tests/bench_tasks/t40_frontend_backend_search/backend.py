"""
Frontend-backend search interface mismatch task.

Backend: full-text search API with filters and sorting
Frontend client: search service

Bugs:
1. backend.py: sort direction 'desc' check uses wrong comparison (sorts ascending when 'desc' requested)
2. backend.py: filter by 'tags' uses AND logic but frontend/tests expect OR logic
3. frontend.py: sends 'sort_order' param but backend expects 'sort_dir'
"""

from typing import Any, Optional


SAMPLE_DOCS = [
    {"id": 1, "title": "Python basics", "body": "Learn Python programming", "tags": ["python", "beginner"], "score": 4.5},
    {"id": 2, "title": "Advanced Python", "body": "Deep dive into Python", "tags": ["python", "advanced"], "score": 4.8},
    {"id": 3, "title": "JavaScript intro", "body": "Getting started with JS", "tags": ["javascript", "beginner"], "score": 4.2},
    {"id": 4, "title": "React tutorial", "body": "Build UIs with React", "tags": ["javascript", "react"], "score": 4.6},
    {"id": 5, "title": "Go concurrency", "body": "Goroutines and channels", "tags": ["go", "advanced"], "score": 4.9},
    {"id": 6, "title": "Docker basics", "body": "Containerize your apps", "tags": ["devops", "beginner"], "score": 4.1},
    {"id": 7, "title": "Kubernetes guide", "body": "Orchestrate containers", "tags": ["devops", "advanced"], "score": 4.7},
    {"id": 8, "title": "SQL fundamentals", "body": "Relational database basics", "tags": ["database", "beginner"], "score": 4.3},
]


def search(query: str = "", tags: list[str] = None, sort_by: str = "score",
           sort_dir: str = "desc", page: int = 1, page_size: int = 10) -> dict:
    """Search documents with optional filtering and sorting.

    tags: filter to docs that have ANY of the given tags (OR logic)
    sort_by: field to sort by ('score', 'title', 'id')
    sort_dir: 'asc' or 'desc'
    """
    results = list(SAMPLE_DOCS)

    # Text filter
    if query:
        q = query.lower()
        results = [d for d in results
                   if q in d["title"].lower() or q in d["body"].lower()]

    # Tag filter
    if tags:
        # Bug: uses AND logic (all tags must match) instead of OR (any tag matches)
        results = [d for d in results
                   if all(t in d["tags"] for t in tags)]

    # Sort
    reverse = sort_dir == "asc"  # Bug: should be sort_dir == "desc"
    if sort_by in ("score", "title", "id"):
        results.sort(key=lambda d: d[sort_by], reverse=reverse)

    # Paginate
    total = len(results)
    start = (page - 1) * page_size
    items = results[start: start + page_size]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": (total + page_size - 1) // page_size if total > 0 else 1,
    }
