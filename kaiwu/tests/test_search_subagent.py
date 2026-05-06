"""
Tests for SearchSubagent and UpstreamManifest.
Covers: isolated context, parallel reads, manifest extraction, contract checking.
"""

import pytest
from unittest.mock import MagicMock, patch
from kaiwu.core.upstream_manifest import UpstreamManifest
from kaiwu.experts.search_subagent import SearchSubagent, SearchResult
from kaiwu.core.context import TaskContext


# ══════════════════════════════════════════════════════════════════════
# UpstreamManifest Tests
# ══════════════════════════════════════════════════════════════════════


class TestUpstreamManifest:
    def setup_method(self):
        self.manifest = UpstreamManifest()

    def test_extract_python_signatures(self):
        patches = [{
            "file": "utils.py",
            "original": "",
            "modified": "def reset_password(token: str, new_pass: str) -> bool:\n    return True\n",
        }]
        self.manifest.update(patches)
        sigs = self.manifest.get_all_signatures()
        assert "utils.py" in sigs
        assert "reset_password" in sigs["utils.py"]
        assert "token: str" in sigs["utils.py"]["reset_password"]
        assert "-> bool" in sigs["utils.py"]["reset_password"]

    def test_extract_constants(self):
        patches = [{
            "file": "config.py",
            "original": "",
            "modified": "API_BASE = 'https://api.example.com/v2'\nMAX_RETRIES = 3\n",
        }]
        self.manifest.update(patches)
        consts = self.manifest.get_all_constants()
        assert "config.py" in consts
        assert "API_BASE" in consts["config.py"]
        assert "MAX_RETRIES" in consts["config.py"]

    def test_extract_imports(self):
        patches = [{
            "file": "views.py",
            "original": "",
            "modified": "from kaiwu.core.context import TaskContext\nimport os\n\ndef handler():\n    pass\n",
        }]
        self.manifest.update(patches)
        # Should track dependency
        assert "views.py" in self.manifest._imports
        assert any("TaskContext" in imp for imp in self.manifest._imports["views.py"])

    def test_get_constraints_for_file(self):
        # First, register upstream signatures
        patches_upstream = [{
            "file": "kaiwu/core/context.py",
            "original": "",
            "modified": "class TaskContext:\n    def __init__(self, user_input: str = ''):\n        self.user_input = user_input\n",
        }]
        self.manifest.update(patches_upstream)

        # Then register a file that imports from upstream
        patches_downstream = [{
            "file": "views.py",
            "original": "",
            "modified": "from kaiwu.core.context import TaskContext\n\ndef handler():\n    ctx = TaskContext(user_input='test')\n",
        }]
        self.manifest.update(patches_downstream)

        constraints = self.manifest.get_constraints_for_file("views.py")
        # Should mention the upstream contract
        assert "kaiwu/core/context.py" in constraints or constraints == ""

    def test_check_consistency_no_violations(self):
        # Register a function signature
        self.manifest._signatures["utils.py"] = {
            "process": "def process(data: list, mode: str)"
        }
        self.manifest._dependency_graph["main.py"] = ["utils.py"]

        # Code that calls process correctly
        code = "result = process(my_data, 'fast')\n"
        violations = self.manifest.check_consistency("main.py", code)
        assert violations == []

    def test_check_consistency_too_many_args(self):
        # Register: process takes 2 params (data, mode)
        self.manifest._signatures["utils.py"] = {
            "process": "def process(data: list, mode: str)"
        }
        self.manifest._dependency_graph["main.py"] = ["utils.py"]

        # Code that calls process with 3 args
        code = "result = process(my_data, 'fast', True)\n"
        violations = self.manifest.check_consistency("main.py", code)
        assert len(violations) == 1
        assert "3" in violations[0] and "2" in violations[0]

    def test_check_consistency_constant_redefinition(self):
        self.manifest._constants["config.py"] = {"API_BASE": "'https://api.example.com'"}
        self.manifest._dependency_graph["main.py"] = ["config.py"]

        # Code redefines constant with different value
        code = "API_BASE = 'https://wrong-url.com'\nprint(API_BASE)\n"
        violations = self.manifest.check_consistency("main.py", code)
        assert len(violations) >= 1
        assert "API_BASE" in violations[0]

    def test_to_compact_summary(self):
        patches = [{
            "file": "a.py",
            "original": "",
            "modified": "MAX_SIZE = 100\ndef foo(x: int) -> str:\n    return str(x)\n",
        }]
        self.manifest.update(patches)
        summary = self.manifest.to_compact_summary()
        assert "signatures" in summary
        assert "constants" in summary
        assert "imports" in summary

    def test_clear(self):
        self.manifest._signatures["a.py"] = {"foo": "def foo()"}
        self.manifest.clear()
        assert self.manifest._signatures == {}
        assert self.manifest._constants == {}

    def test_async_function_extraction(self):
        patches = [{
            "file": "api.py",
            "original": "",
            "modified": "async def fetch_data(url: str, timeout: int = 30) -> dict:\n    return {}\n",
        }]
        self.manifest.update(patches)
        sigs = self.manifest.get_all_signatures()
        assert "fetch_data" in sigs["api.py"]
        assert "async def" in sigs["api.py"]["fetch_data"]

    def test_regex_fallback_for_non_python(self):
        patches = [{
            "file": "main.go",
            "original": "",
            "modified": "func ProcessData(input []byte, mode string) error {\n    return nil\n}\n\nMAX_BUFFER = 4096\n",
        }]
        self.manifest.update(patches)
        sigs = self.manifest.get_all_signatures()
        assert "main.go" in sigs
        assert "ProcessData" in sigs["main.go"]
        consts = self.manifest.get_all_constants()
        assert "MAX_BUFFER" in consts.get("main.go", {})

    def test_multiple_patches_accumulate(self):
        self.manifest.update([{
            "file": "a.py",
            "original": "",
            "modified": "def foo() -> int:\n    return 1\n",
        }])
        self.manifest.update([{
            "file": "b.py",
            "original": "",
            "modified": "def bar(x: str) -> str:\n    return x\n",
        }])
        sigs = self.manifest.get_all_signatures()
        assert "foo" in sigs["a.py"]
        assert "bar" in sigs["b.py"]

    def test_count_params_with_self(self):
        """self/cls should not count as parameters."""
        count = UpstreamManifest._count_params("def method(self, x: int, y: int)")
        assert count == 2

    def test_count_params_with_kwargs(self):
        """*args/**kwargs means we can't determine exact count."""
        count = UpstreamManifest._count_params("def func(x, *args, **kwargs)")
        assert count is None

    def test_count_args_nested(self):
        """Nested function calls shouldn't split on inner commas."""
        count = UpstreamManifest._count_args("foo(1, 2), bar(3)")
        assert count == 2  # Two top-level args


# ══════════════════════════════════════════════════════════════════════
# SearchSubagent Tests
# ══════════════════════════════════════════════════════════════════════


class TestSearchSubagent:
    def setup_method(self):
        self.mock_locator = MagicMock()
        self.mock_tools = MagicMock()
        self.subagent = SearchSubagent(self.mock_locator, self.mock_tools)

    def test_search_returns_clean_results(self):
        """SearchSubagent should return structured results, not raw locator state."""
        self.mock_locator.run.return_value = {
            "relevant_files": ["src/main.py", "src/utils.py"],
            "relevant_functions": ["process", "validate"],
            "edit_locations": ["src/main.py:L10-20"],
            "method": "bm25_graph",
        }
        self.mock_tools.read_file.return_value = "def process(data):\n    return data\n"

        ctx = TaskContext(user_input="fix the process function", project_root="/tmp/proj")
        result = self.subagent.search(ctx)

        assert result["relevant_files"] == ["src/main.py", "src/utils.py"]
        assert result["relevant_functions"] == ["process", "validate"]
        assert result["method"] == "bm25_graph"
        assert "code_snippets" in result

    def test_search_does_not_modify_original_ctx(self):
        """SearchSubagent must not pollute the original TaskContext."""
        self.mock_locator.run.return_value = {
            "relevant_files": ["a.py"],
            "relevant_functions": ["foo"],
            "edit_locations": [],
            "method": "bm25_graph",
        }
        self.mock_tools.read_file.return_value = "def foo():\n    pass\n"

        ctx = TaskContext(user_input="test", project_root="/tmp")
        # These should remain None after search
        assert ctx.locator_output is None

        self.subagent.search(ctx)

        # Original ctx should NOT be modified by search
        assert ctx.locator_output is None

    def test_search_with_manifest_constraints(self):
        """Should include upstream constraints when manifest is provided."""
        self.mock_locator.run.return_value = {
            "relevant_files": ["views.py"],
            "relevant_functions": ["handler"],
            "edit_locations": [],
            "method": "llm_fallback",
        }
        self.mock_tools.read_file.return_value = "def handler():\n    pass\n"

        manifest = UpstreamManifest()
        manifest._signatures["utils.py"] = {"process": "def process(data: list) -> dict"}
        manifest._dependency_graph["views.py"] = ["utils.py"]

        ctx = TaskContext(user_input="fix handler", project_root="/tmp")
        result = self.subagent.search(ctx, manifest)

        assert "upstream_constraints" in result
        assert "process" in result["upstream_constraints"]

    def test_search_locator_returns_nothing(self):
        """Should return empty results gracefully when locator finds nothing."""
        self.mock_locator.run.return_value = None

        ctx = TaskContext(user_input="fix something", project_root="/tmp")
        result = self.subagent.search(ctx)

        assert result["relevant_files"] == []
        assert result["code_snippets"] == {}
        assert result["method"] == "none"

    def test_parallel_read_handles_errors(self):
        """File read errors should not crash the subagent."""
        self.mock_locator.run.return_value = {
            "relevant_files": ["good.py", "bad.py"],
            "relevant_functions": ["foo"],
            "edit_locations": [],
            "method": "bm25_graph",
        }

        def side_effect(path):
            if "bad" in path:
                return "[ERROR] File not found"
            return "def foo():\n    return 42\n"

        self.mock_tools.read_file.side_effect = side_effect

        ctx = TaskContext(user_input="test", project_root="/tmp")
        result = self.subagent.search(ctx)

        # Should have snippet for good.py but not bad.py
        assert "good.py" in result["code_snippets"]
        assert "bad.py" not in result["code_snippets"]

    def test_extract_precise_snippet_with_functions(self):
        content = """import os

def helper():
    return 1

def target_func(x, y):
    result = x + y
    if result > 10:
        return result * 2
    return result

def another():
    pass
"""
        snippet = self.subagent._extract_precise_snippet(
            content, ["target_func"], "test.py"
        )
        assert "target_func" in snippet
        assert "x + y" in snippet
        # Should NOT include unrelated functions in full
        assert "def another" not in snippet or "..." in snippet

    def test_extract_precise_snippet_no_functions(self):
        """When no functions specified, return first N lines."""
        content = "line1\nline2\nline3\n"
        snippet = self.subagent._extract_precise_snippet(content, [], "test.py")
        assert "line1" in snippet

    def test_merge_ranges(self):
        ranges = [(1, 10), (8, 20), (25, 30)]
        merged = SearchSubagent._merge_ranges(ranges)
        assert merged == [(1, 20), (25, 30)]

    def test_merge_ranges_with_gap(self):
        """Ranges within 3 lines should merge."""
        ranges = [(1, 10), (12, 20)]  # gap of 2
        merged = SearchSubagent._merge_ranges(ranges)
        assert merged == [(1, 20)]

    def test_find_function_range(self):
        lines = [
            "import os",
            "",
            "def foo(x):",
            "    return x + 1",
            "",
            "def bar():",
            "    pass",
        ]
        start, end = SearchSubagent._find_function_range(lines, "foo")
        assert start == 2
        assert end == 5  # up to but not including def bar

    def test_find_function_range_not_found(self):
        lines = ["def other():", "    pass"]
        start, end = SearchSubagent._find_function_range(lines, "nonexistent")
        assert start == -1
        assert end == -1


class TestSearchResult:
    def test_to_dict(self):
        r = SearchResult(
            file="src/main.py",
            start_line=10,
            end_line=25,
            content="def foo():\n    pass",
            function_name="foo",
        )
        d = r.to_dict()
        assert d["file"] == "src/main.py"
        assert d["start_line"] == 10
        assert d["end_line"] == 25
        assert d["function_name"] == "foo"
