"""
Tests for Blueprint dataclass and _generate_blueprint JSON parsing.
验证：Blueprint字段完整性、JSON三重fallback、BlueprintCollector双写。
"""

import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kaiwu.core.blueprint import Blueprint
from kaiwu.flywheel.blueprint_collector import BlueprintCollector


class TestBlueprint:
    """Blueprint dataclass基本测试。"""

    def test_fields_complete(self):
        """所有字段都能正确设置。"""
        bp = Blueprint(
            target_file="src/calc.py",
            target_function="add",
            operation="添加空值检查",
            logic_description="检查a和b是否为None",
            pattern="null_check",
            requires=["typing"],
            constraints=["add(a,b) -> int"],
            raw_llm_output='{"operation":"添加空值检查"}',
        )
        assert bp.target_file == "src/calc.py"
        assert bp.target_function == "add"
        assert bp.operation == "添加空值检查"
        assert bp.pattern == "null_check"
        assert len(bp.requires) == 1
        assert len(bp.constraints) == 1

    def test_defaults(self):
        """默认值正确。"""
        bp = Blueprint(
            target_file="a.py",
            target_function="f",
            operation="fix",
            logic_description="fix bug",
            pattern="",
        )
        assert bp.requires == []
        assert bp.constraints == []
        assert bp.raw_llm_output == ""


class TestGenerateBlueprintParsing:
    """测试Generator._generate_blueprint的JSON解析逻辑（独立测试）。"""

    def _parse_json(self, raw: str) -> dict | None:
        """模拟_generate_blueprint里的JSON提取逻辑。"""
        import re
        json_str = None
        # 1. 找```json...```块
        m = re.search(r'```json\s*(.*?)\s*```', raw, re.DOTALL)
        if m:
            json_str = m.group(1)
        # 2. 找第一个{到最后一个}
        if not json_str:
            start = raw.find('{')
            end = raw.rfind('}')
            if start != -1 and end > start:
                json_str = raw[start:end+1]
        # 3. 都失败
        if not json_str:
            return None
        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            return None

    def test_pure_json(self):
        """纯JSON输入。"""
        raw = '{"operation":"fix bug","logic":"修复排序","pattern":"","requires":[]}'
        data = self._parse_json(raw)
        assert data is not None
        assert data["operation"] == "fix bug"

    def test_json_with_prefix_suffix(self):
        """前缀文字 + JSON + 后缀文字。"""
        raw = '好的，这是施工图：\n{"operation":"add check","logic":"null guard","pattern":"null_check","requires":["typing"]}\n完成。'
        data = self._parse_json(raw)
        assert data is not None
        assert data["pattern"] == "null_check"

    def test_json_in_code_block(self):
        """```json...```代码块。"""
        raw = '```json\n{"operation":"implement","logic":"实现函数","pattern":"stub_implement","requires":[]}\n```'
        data = self._parse_json(raw)
        assert data is not None
        assert data["operation"] == "implement"

    def test_garbage_returns_none(self):
        """乱码返回None。"""
        raw = "这不是JSON，也没有大括号"
        data = self._parse_json(raw)
        assert data is None

    def test_invalid_json_returns_none(self):
        """格式错误的JSON返回None。"""
        raw = '{"operation": "fix", "logic": }'  # 语法错误
        data = self._parse_json(raw)
        assert data is None

    def test_nested_braces(self):
        """嵌套大括号正确处理。"""
        raw = '{"operation":"fix","logic":"if x > 0 { return }","pattern":"","requires":[]}'
        data = self._parse_json(raw)
        assert data is not None
        assert data["operation"] == "fix"


class TestBlueprintCollector:
    """BlueprintCollector双写和候选提取测试。"""

    def test_record_success(self, tmp_path):
        """成功任务写入JSONL。"""
        collector = BlueprintCollector()
        # 覆盖全局路径避免污染
        collector.GLOBAL_FILE = tmp_path / "global.jsonl"

        bp = Blueprint(
            target_file="a.py",
            target_function="foo",
            operation="fix null",
            logic_description="add check",
            pattern="null_check",
        )
        collector.record(bp, "def foo():\n    pass", True, "template", str(tmp_path))

        # 项目级文件
        project_file = tmp_path / ".kaiwu" / "blueprints.jsonl"
        assert project_file.exists()
        lines = project_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["operation"] == "fix null"
        assert entry["source"] == "template"

        # 全局文件
        assert collector.GLOBAL_FILE.exists()

    def test_skip_failure(self, tmp_path):
        """失败任务不记录。"""
        collector = BlueprintCollector()
        collector.GLOBAL_FILE = tmp_path / "global.jsonl"

        bp = Blueprint(
            target_file="a.py",
            target_function="foo",
            operation="fix",
            logic_description="fix",
            pattern="",
        )
        collector.record(bp, "code", False, "llm_direct", str(tmp_path))
        assert not collector.GLOBAL_FILE.exists()

    def test_skip_none_blueprint(self, tmp_path):
        """blueprint为None不记录。"""
        collector = BlueprintCollector()
        collector.GLOBAL_FILE = tmp_path / "global.jsonl"
        collector.record(None, "code", True, "llm_direct", str(tmp_path))
        assert not collector.GLOBAL_FILE.exists()

    def test_skip_empty_code(self, tmp_path):
        """空代码不记录。"""
        collector = BlueprintCollector()
        collector.GLOBAL_FILE = tmp_path / "global.jsonl"
        bp = Blueprint(
            target_file="a.py", target_function="f",
            operation="x", logic_description="y", pattern="",
        )
        collector.record(bp, "", True, "llm_direct", str(tmp_path))
        assert not collector.GLOBAL_FILE.exists()

    def test_get_llm_direct_candidates(self, tmp_path):
        """高频llm_direct操作被识别为候选。"""
        collector = BlueprintCollector()
        collector.GLOBAL_FILE = tmp_path / "global.jsonl"

        bp = Blueprint(
            target_file="a.py", target_function="f",
            operation="add error handling", logic_description="try/except",
            pattern="",
        )
        # 写入3次相同operation
        for _ in range(3):
            collector.record(bp, "def f():\n    try: pass\n    except: pass",
                          True, "llm_direct", str(tmp_path))

        candidates = collector.get_llm_direct_candidates(min_count=3)
        assert len(candidates) == 1
        assert candidates[0]["operation"] == "add error handling"

    def test_get_candidates_below_threshold(self, tmp_path):
        """低于阈值不返回。"""
        collector = BlueprintCollector()
        collector.GLOBAL_FILE = tmp_path / "global.jsonl"

        bp = Blueprint(
            target_file="a.py", target_function="f",
            operation="rare op", logic_description="x", pattern="",
        )
        collector.record(bp, "code", True, "llm_direct", str(tmp_path))
        candidates = collector.get_llm_direct_candidates(min_count=3)
        assert len(candidates) == 0

    def test_write_failure_silent(self, tmp_path):
        """写入失败不抛异常。"""
        collector = BlueprintCollector()
        # 设置一个不可写的路径
        collector.GLOBAL_FILE = Path("/nonexistent/path/file.jsonl")
        bp = Blueprint(
            target_file="a.py", target_function="f",
            operation="x", logic_description="y", pattern="",
        )
        # 不应抛异常
        collector.record(bp, "code", True, "llm_direct", str(tmp_path))
