"""Frontend search client."""

from typing import Optional
from backend import search


class SearchClient:
    """Frontend client for search operations."""

    def __init__(self, page_size: int = 10):
        self.page_size = page_size
        self._last_result: Optional[dict] = None

    def _build_params(self, query: str, tags: list[str] = None,
                      sort_by: str = "score", sort_dir: str = "desc",
                      page: int = 1) -> dict:
        params = {
            "query": query,
            "sort_by": sort_by,
            # Bug: sends 'sort_order' but backend expects 'sort_dir'
            "sort_order": sort_dir,
            "page": page,
            "page_size": self.page_size,
        }
        if tags:
            params["tags"] = tags
        return params

    def query(self, query: str = "", tags: list[str] = None,
              sort_by: str = "score", sort_dir: str = "desc",
              page: int = 1) -> dict:
        """Execute a search query."""
        params = self._build_params(query, tags, sort_by, sort_dir, page)
        # Simulate calling backend with params
        response = search(
            query=params.get("query", ""),
            tags=params.get("tags"),
            sort_by=params.get("sort_by", "score"),
            sort_dir=params.get("sort_dir", params.get("sort_order", "desc")),
            page=params.get("page", 1),
            page_size=params.get("page_size", 10),
        )
        self._last_result = response
        return response

    def total_results(self) -> int:
        if self._last_result is None:
            return 0
        return self._last_result.get("total", 0)

    def total_pages(self) -> int:
        if self._last_result is None:
            return 0
        return self._last_result.get("page_count", 0)
