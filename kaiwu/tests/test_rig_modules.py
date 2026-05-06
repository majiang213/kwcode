"""
Tests for the RIG (Repository Intelligence Graph) modules:
- Task 1: GraphBuilder.export_rig()
- Task 2: upstream_summary structured dict
- Task 3: ConsistencyChecker
- Task 4: Gate/Locator prompt rig.json引导
"""

import json
import os
import tempfile
import shutil
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════
# Task 1: export_rig() tests
# ═══════════════════════════════════════════════════════════════════

class TestExportRig:
    """Tests for GraphBuilder.export_rig() method."""

    def _make_project(self, tmp_path, files: dict):
        """Create a temp project with given file contents."""
        for rel_path, content in files.items():
            fpath = os.path.join(str(tmp_path), rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
        return str(tmp_path)

    def test_basic_exports_imports(self, tmp_path):
        """export_rig extracts top-level defs and imports."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = self._make_project(tmp_path, {
            "src/auth.py": "import os\nfrom db import connect\n\ndef login():\n    pass\n\ndef logout():\n    pass\n",
            "src/db.py": "import sqlite3\n\nclass Database:\n    pass\n",
        })

        gb = GraphBuilder(project)
        rig = gb.export_rig()

        assert "src/auth.py" in rig["files"]
        auth = rig["files"]["src/auth.py"]
        assert "login" in auth["exports"]
        assert "logout" in auth["exports"]
        assert "os" in auth["imports"]
        assert "db" in auth["imports"]

        assert "src/db.py" in rig["files"]
        db = rig["files"]["src/db.py"]
        assert "Database" in db["exports"]
        assert "sqlite3" in db["imports"]

    def test_api_routes_flask(self, tmp_path):
        """export_rig detects Flask-style route decorators."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = self._make_project(tmp_path, {
            "app.py": '@app.post("/login")\ndef handle_login():\n    pass\n\n@app.get("/users")\ndef list_users():\n    pass\n',
        })

        gb = GraphBuilder(project)
        rig = gb.export_rig()

        assert "POST /login" in rig["api_routes"]
        assert rig["api_routes"]["POST /login"] == "app.py:handle_login"
        assert "GET /users" in rig["api_routes"]
        assert rig["api_routes"]["GET /users"] == "app.py:list_users"

    def test_api_routes_fastapi_router(self, tmp_path):
        """export_rig detects FastAPI router-style decorators."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = self._make_project(tmp_path, {
            "routes/user.py": '@router.delete("/user/{id}")\ndef delete_user():\n    pass\n',
        })

        gb = GraphBuilder(project)
        rig = gb.export_rig()

        assert "DELETE /user/{id}" in rig["api_routes"]
        assert "delete_user" in rig["api_routes"]["DELETE /user/{id}"]

    def test_test_coverage_mapping(self, tmp_path):
        """export_rig maps test_foo.py -> foo.py."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = self._make_project(tmp_path, {
            "src/auth.py": "def login():\n    pass\n",
            "tests/test_auth.py": "def test_login():\n    pass\n",
        })

        gb = GraphBuilder(project)
        rig = gb.export_rig()

        assert "src/auth.py" in rig["test_coverage"]
        assert "tests/test_auth.py" in rig["test_coverage"]["src/auth.py"]

    def test_frontend_axios_calls(self, tmp_path):
        """export_rig detects axios API calls in JS files."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = self._make_project(tmp_path, {
            "frontend/api.js": 'const login = async () => {\n  axios.post("/api/login", data)\n}\n',
        })

        gb = GraphBuilder(project)
        rig = gb.export_rig()

        assert "POST /api/login" in rig["frontend_api_calls"]
        assert "login" in rig["frontend_api_calls"]["POST /api/login"]

    def test_frontend_fetch_calls(self, tmp_path):
        """export_rig detects fetch() with method in JS files."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = self._make_project(tmp_path, {
            "frontend/api.ts": 'const deleteUser = () => {\n  fetch("/api/user", {method: "DELETE"})\n}\n',
        })

        gb = GraphBuilder(project)
        rig = gb.export_rig()

        assert "DELETE /api/user" in rig["frontend_api_calls"]

    def test_writes_rig_json_file(self, tmp_path):
        """export_rig creates .kaiwu/rig.json and rig_summary.json on disk."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = self._make_project(tmp_path, {
            "main.py": "def main():\n    pass\n",
        })

        gb = GraphBuilder(project)
        gb.export_rig()

        rig_path = os.path.join(project, ".kaiwu", "rig.json")
        summary_path = os.path.join(project, ".kaiwu", "rig_summary.json")
        assert os.path.exists(rig_path)
        assert os.path.exists(summary_path)
        with open(rig_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "files" in data
        assert "api_routes" in data
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
        # Summary has file list (paths only), not exports/imports detail
        assert isinstance(summary["files"], list)
        assert "api_routes" in summary

    def test_empty_project(self, tmp_path):
        """export_rig handles empty project gracefully."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = str(tmp_path)
        gb = GraphBuilder(project)
        rig = gb.export_rig()

        assert rig["files"] == {}
        assert rig["api_routes"] == {}
        assert rig["test_coverage"] == {}
        assert rig["frontend_api_calls"] == {}

    def test_skips_venv_and_git(self, tmp_path):
        """export_rig skips .git and venv directories."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        project = self._make_project(tmp_path, {
            "src/main.py": "def main():\n    pass\n",
            "venv/lib/site.py": "def site_func():\n    pass\n",
            ".git/hooks/pre-commit": "#!/bin/sh\n",
        })

        gb = GraphBuilder(project)
        rig = gb.export_rig()

        assert "src/main.py" in rig["files"]
        assert not any("venv" in k for k in rig["files"])
        assert not any(".git" in k for k in rig["files"])


# ═══════════════════════════════════════════════════════════════════
# Task 2: upstream_summary structured dict tests
# ═══════════════════════════════════════════════════════════════════

class TestUpstreamSummary:
    """Tests for structured upstream_summary in TaskContext and TaskCompiler."""

    def test_context_upstream_summary_is_dict(self):
        """upstream_summary defaults to empty dict."""
        from kaiwu.core.context import TaskContext
        ctx = TaskContext()
        assert isinstance(ctx.upstream_summary, dict)
        assert ctx.upstream_summary == {}

    def test_build_dependency_context_returns_dict(self):
        """_build_dependency_context returns structured dict."""
        from kaiwu.core.task_compiler import TaskCompiler

        # Mock completed results
        ctx_mock = MagicMock()
        ctx_mock.generator_output = {
            "patches": [
                {"file": "src/auth.py", "modified": "def login():\n    return True\n\ndef logout_v2():\n    pass\n"},
                {"file": "src/db.py", "modified": "def connect():\n    pass\n"},
            ],
        }
        completed = {
            "t1": {"success": True, "context": ctx_mock},
        }

        result = TaskCompiler._build_dependency_context(["t1"], completed)

        assert isinstance(result, dict)
        assert "src/auth.py" in result["modified_files"]
        assert "src/db.py" in result["modified_files"]
        assert "src/auth.py" in result["diffs"]
        assert "login" in result["new_symbols"]
        assert "logout_v2" in result["new_symbols"]
        assert "connect" in result["new_symbols"]
        assert result["broken_interfaces"] == []

    def test_build_dependency_context_truncates_diffs(self):
        """Diffs are truncated to 200 lines."""
        from kaiwu.core.task_compiler import TaskCompiler

        long_code = "\n".join([f"line_{i} = {i}" for i in range(300)])
        ctx_mock = MagicMock()
        ctx_mock.generator_output = {
            "patches": [{"file": "big.py", "modified": long_code}],
        }
        completed = {"t1": {"success": True, "context": ctx_mock}}

        result = TaskCompiler._build_dependency_context(["t1"], completed)

        lines = result["diffs"]["big.py"].splitlines()
        assert len(lines) == 201  # 200 + "... (truncated)"
        assert "truncated" in lines[-1]

    def test_build_dependency_context_handles_missing(self):
        """Gracefully handles missing context or generator_output."""
        from kaiwu.core.task_compiler import TaskCompiler

        completed = {
            "t1": {"success": False, "context": None},
            "t2": {"success": True, "context": MagicMock(generator_output=None)},
        }

        result = TaskCompiler._build_dependency_context(["t1", "t2"], completed)

        assert result["modified_files"] == []
        assert result["diffs"] == {}
        assert result["new_symbols"] == []

    def test_format_upstream_text(self):
        """_format_upstream_text produces readable text."""
        from kaiwu.core.task_compiler import TaskCompiler

        upstream = {
            "modified_files": ["src/auth.py"],
            "diffs": {"src/auth.py": "def login():\n    pass"},
            "new_symbols": ["login"],
            "broken_interfaces": [],
        }

        text = TaskCompiler._format_upstream_text(upstream)

        assert "src/auth.py" in text
        assert "login" in text
        assert "修改文件" in text

    def test_execute_task_sets_upstream_summary(self):
        """_execute_task stores structured upstream_summary on result context."""
        from kaiwu.core.task_compiler import TaskCompiler

        orch = MagicMock()
        gate = MagicMock()
        gate.classify.return_value = {"expert_type": "codegen", "task_summary": "t", "difficulty": "easy"}

        result_ctx = MagicMock()
        result_ctx.upstream_summary = {}
        orch.run.return_value = {"success": True, "context": result_ctx, "error": None, "elapsed": 0.1}

        # Upstream completed task
        upstream_ctx = MagicMock()
        upstream_ctx.generator_output = {
            "patches": [{"file": "a.py", "modified": "def foo():\n    pass\n"}],
        }
        completed = {"t1": {"success": True, "context": upstream_ctx}}

        compiler = TaskCompiler(orch, gate, "/tmp/test")
        task_def = {"id": "t2", "input": "do something", "depends_on": ["t1"]}

        result = compiler._execute_task(task_def, completed, None)

        assert result["context"].upstream_summary["modified_files"] == ["a.py"]
        assert "foo" in result["context"].upstream_summary["new_symbols"]


# ═══════════════════════════════════════════════════════════════════
# Task 3: ConsistencyChecker tests
# ═══════════════════════════════════════════════════════════════════

class TestConsistencyChecker:
    """Tests for ConsistencyChecker deterministic API alignment check."""

    def test_consistent_when_matched(self):
        """Returns consistent=True when all routes match."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {
            "api_routes": {"POST /login": "src/auth.py:login", "GET /users": "src/user.py:list"},
            "frontend_api_calls": {"POST /login": "frontend/api.js:login", "GET /users": "frontend/api.js:getUsers"},
        }

        cc = ConsistencyChecker("/tmp/fake")
        result = cc.check(rig)

        assert result["consistent"] is True
        assert len(result["matched"]) == 2
        assert result["backend_only"] == []
        assert result["frontend_only"] == []

    def test_detects_backend_only(self):
        """Detects routes that exist in backend but not frontend."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {
            "api_routes": {"POST /login": "auth.py:login", "DELETE /user": "user.py:delete"},
            "frontend_api_calls": {"POST /login": "api.js:login"},
        }

        cc = ConsistencyChecker("/tmp/fake")
        result = cc.check(rig)

        assert result["consistent"] is False
        assert "DELETE /user" in result["backend_only"]
        assert result["frontend_only"] == []

    def test_detects_frontend_only(self):
        """Detects routes called by frontend but missing in backend."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {
            "api_routes": {"POST /login": "auth.py:login"},
            "frontend_api_calls": {"POST /login": "api.js:login", "PUT /profile": "api.js:updateProfile"},
        }

        cc = ConsistencyChecker("/tmp/fake")
        result = cc.check(rig)

        assert result["consistent"] is False
        assert "PUT /profile" in result["frontend_only"]

    def test_normalizes_trailing_slash(self):
        """Routes with/without trailing slash are treated as same."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {
            "api_routes": {"GET /users/": "user.py:list"},
            "frontend_api_calls": {"GET /users": "api.js:getUsers"},
        }

        cc = ConsistencyChecker("/tmp/fake")
        result = cc.check(rig)

        assert result["consistent"] is True

    def test_case_insensitive_method(self):
        """HTTP methods are compared case-insensitively."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {
            "api_routes": {"post /login": "auth.py:login"},
            "frontend_api_calls": {"POST /login": "api.js:login"},
        }

        cc = ConsistencyChecker("/tmp/fake")
        result = cc.check(rig)

        assert result["consistent"] is True

    def test_check_with_details(self):
        """check_with_details returns location info for inconsistencies."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {
            "api_routes": {"POST /login": "src/auth.py:login", "DELETE /session": "src/auth.py:logout"},
            "frontend_api_calls": {"POST /login": "frontend/api.js:login"},
        }

        cc = ConsistencyChecker("/tmp/fake")
        result = cc.check_with_details(rig)

        assert result["consistent"] is False
        assert len(result["inconsistencies"]) == 1
        item = result["inconsistencies"][0]
        assert item["type"] == "backend_only"
        assert "DELETE" in item["route"]
        assert item["backend_location"] == "src/auth.py:logout"

    def test_format_for_subtask(self):
        """format_for_subtask produces readable text."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {
            "api_routes": {"POST /login": "auth.py:login", "DELETE /user": "user.py:delete"},
            "frontend_api_calls": {"POST /login": "api.js:login"},
        }

        cc = ConsistencyChecker("/tmp/fake")
        text = cc.format_for_subtask(rig)

        assert "不一致" in text
        assert "DELETE /user" in text
        assert "后端独有" in text

    def test_format_for_subtask_consistent(self):
        """format_for_subtask returns pass message when consistent."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {
            "api_routes": {"GET /health": "app.py:health"},
            "frontend_api_calls": {"GET /health": "api.js:check"},
        }

        cc = ConsistencyChecker("/tmp/fake")
        text = cc.format_for_subtask(rig)

        assert "通过" in text

    def test_missing_rig_json(self, tmp_path):
        """Returns graceful result when rig.json doesn't exist."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        cc = ConsistencyChecker(str(tmp_path))
        result = cc.check()

        assert result["consistent"] is True
        assert "error" in result

    def test_empty_routes(self):
        """Handles empty api_routes and frontend_api_calls."""
        from kaiwu.experts.consistency_checker import ConsistencyChecker

        rig = {"api_routes": {}, "frontend_api_calls": {}}

        cc = ConsistencyChecker("/tmp/fake")
        result = cc.check(rig)

        assert result["consistent"] is True
        assert result["total_backend"] == 0
        assert result["total_frontend"] == 0


# ═══════════════════════════════════════════════════════════════════
# Task 4: Gate/Locator prompt rig.json引导 tests
# ═══════════════════════════════════════════════════════════════════

class TestRigPromptGuidance:
    """Tests that Gate and Locator prompts reference rig.json."""

    def test_gate_prompt_mentions_rig(self):
        """Gate prompt includes rig.json guidance."""
        from kaiwu.core.gate import GATE_PROMPT
        assert "rig.json" in GATE_PROMPT

    def test_locator_file_prompt_has_rig_placeholder(self):
        """Locator file prompt includes {rig_context} placeholder."""
        from kaiwu.experts.locator import LOCATOR_FILE_PROMPT
        assert "{rig_context}" in LOCATOR_FILE_PROMPT
        assert "rig.json" in LOCATOR_FILE_PROMPT

    def test_locator_load_rig_context_missing(self, tmp_path):
        """_load_rig_context returns empty string when no rig.json."""
        from kaiwu.experts.locator import LocatorExpert
        from unittest.mock import MagicMock

        locator = LocatorExpert(llm=MagicMock(), tool_executor=MagicMock())
        result = locator._load_rig_context(str(tmp_path))
        assert result == ""

    def test_locator_load_rig_context_present(self, tmp_path):
        """_load_rig_context returns summary when rig_summary.json exists."""
        from kaiwu.experts.locator import LocatorExpert
        from unittest.mock import MagicMock

        # Create rig_summary.json (not rig.json — locator reads the summary)
        kaiwu_dir = os.path.join(str(tmp_path), ".kaiwu")
        os.makedirs(kaiwu_dir)
        rig_summary = {
            "files": ["src/auth.py", "frontend/api.js"],
            "api_routes": {"POST /login": "src/auth.py:login"},
            "test_coverage": {"src/auth.py": ["tests/test_auth.py"]},
            "frontend_api_calls": {"POST /login": "frontend/api.js:doLogin"},
        }
        with open(os.path.join(kaiwu_dir, "rig_summary.json"), "w", encoding="utf-8") as f:
            json.dump(rig_summary, f)

        locator = LocatorExpert(llm=MagicMock(), tool_executor=MagicMock())
        result = locator._load_rig_context(str(tmp_path))

        assert "POST /login" in result
        assert "src/auth.py:login" in result
        assert "frontend/api.js:doLogin" in result
        assert "tests/test_auth.py" in result
