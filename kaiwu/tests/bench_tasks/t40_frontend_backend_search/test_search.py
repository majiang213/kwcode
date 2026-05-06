"""Tests for frontend-backend search interface consistency."""

import pytest
from backend import search
from frontend import SearchClient


class TestBackendSearch:
    def test_returns_all_without_filters(self):
        result = search()
        assert result["total"] == 8

    def test_text_filter(self):
        result = search(query="python")
        assert result["total"] == 2
        titles = [d["title"] for d in result["items"]]
        assert all("python" in t.lower() or "Python" in t for t in titles)

    def test_sort_desc_by_score(self):
        """Default sort should be descending by score (highest first)."""
        result = search(sort_by="score", sort_dir="desc")
        scores = [d["score"] for d in result["items"]]
        assert scores == sorted(scores, reverse=True)

    def test_sort_asc_by_score(self):
        result = search(sort_by="score", sort_dir="asc")
        scores = [d["score"] for d in result["items"]]
        assert scores == sorted(scores)

    def test_sort_asc_by_title(self):
        result = search(sort_by="title", sort_dir="asc")
        titles = [d["title"] for d in result["items"]]
        assert titles == sorted(titles)

    def test_tag_filter_or_logic(self):
        """Filtering by multiple tags should return docs with ANY of the tags."""
        result = search(tags=["python", "go"])
        assert result["total"] == 3  # 2 python + 1 go
        for doc in result["items"]:
            assert any(t in doc["tags"] for t in ["python", "go"])

    def test_tag_filter_single(self):
        result = search(tags=["beginner"])
        assert result["total"] == 4  # python, js, docker, sql

    def test_tag_filter_no_match(self):
        result = search(tags=["nonexistent"])
        assert result["total"] == 0

    def test_pagination(self):
        result = search(page=1, page_size=3)
        assert len(result["items"]) == 3
        assert result["page_count"] == 3

    def test_page_count_calculation(self):
        result = search(page=1, page_size=5)
        assert result["page_count"] == 2


class TestFrontendSearchClient:
    def test_basic_query(self):
        client = SearchClient()
        result = client.query()
        assert result["total"] == 8

    def test_query_with_text(self):
        client = SearchClient()
        result = client.query(query="python")
        assert result["total"] == 2

    def test_query_with_tags_or_logic(self):
        client = SearchClient()
        result = client.query(tags=["python", "go"])
        assert result["total"] == 3

    def test_sort_desc_default(self):
        client = SearchClient()
        result = client.query(sort_by="score", sort_dir="desc")
        scores = [d["score"] for d in result["items"]]
        assert scores == sorted(scores, reverse=True)

    def test_total_results_after_query(self):
        client = SearchClient()
        client.query(query="python")
        assert client.total_results() == 2

    def test_total_pages(self):
        client = SearchClient(page_size=3)
        client.query()
        assert client.total_pages() == 3
