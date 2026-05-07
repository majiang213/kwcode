"""
Tests for SkillParser and SkillExecutor.
验证：模板解析、精确匹配、模糊匹配、AST参数提取、safe_substitute安全性。
"""

import pytest
from string import Template
from kaiwu.core.skill_parser import SkillParser
from kaiwu.core.skill_executor import (
    SkillExecutor, SOURCE_TEMPLATE, SOURCE_TEMPLATE_FUZZY,
    SOURCE_TEMPLATE_FAILED, SOURCE_LLM_DIRECT,
)
from kaiwu.core.blueprint import Blueprint


SAMPLE_SKILL_MD = '''
# BugFix Expert

## 代码模板

```template:null_check
def ${function_name}(${args}):
    if ${check_target} is None:
        raise ValueError("${check_target} cannot be None")
    ${original_body}
```

```template:stub_implement
def ${function_name}(${args}):
    """${logic}"""
    ${original_body}
```

```template:add_return_guard
def ${function_name}(${args}):
    result = None
    ${original_body}
    if result is None:
        return ${default_return}
    return result
```
'''


class TestSkillParser:
    """测试SKILL.md模板解析。"""

    def test_parse_all_templates(self):
        """3个模板都能解析出来。"""
        parser = SkillParser()
        templates = parser.parse_templates(SAMPLE_SKILL_MD)
        assert len(templates) == 3
        assert "null_check" in templates
        assert "stub_implement" in templates
        assert "add_return_guard" in templates

    def test_template_is_string_template(self):
        """解析结果是string.Template对象。"""
        parser = SkillParser()
        templates = parser.parse_templates(SAMPLE_SKILL_MD)
        assert isinstance(templates["null_check"], Template)

    def test_template_has_variables(self):
        """模板包含正确的变量占位符。"""
        parser = SkillParser()
        templates = parser.parse_templates(SAMPLE_SKILL_MD)
        # safe_substitute不会报错，即使缺少变量
        result = templates["null_check"].safe_substitute(
            function_name="foo", args="x", check_target="x",
            original_body="return x"
        )
        assert "def foo(x):" in result
        assert "if x is None:" in result

    def test_empty_content(self):
        """空内容返回空dict。"""
        parser = SkillParser()
        assert parser.parse_templates("") == {}
        assert parser.parse_templates("no templates here") == {}

    def test_malformed_template_skipped(self):
        """格式不对的块不会被解析。"""
        parser = SkillParser()
        content = '```python\nprint("hello")\n```'
        assert parser.parse_templates(content) == {}


class TestSkillExecutor:
    """测试模板填充执行器。"""

    @pytest.fixture
    def executor(self, tmp_path):
        """创建带模板的executor。"""
        # 写一个SKILL.md到临时目录
        skill_dir = tmp_path / "builtin" / "bugfix"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SAMPLE_SKILL_MD, encoding="utf-8")
        return SkillExecutor(str(tmp_path / "builtin"), str(tmp_path))

    def test_exact_match(self, executor):
        """精确匹配模板名。"""
        bp = Blueprint(
            target_file="test.py",
            target_function="process",
            operation="add null check",
            logic_description="check input",
            pattern="null_check",
        )
        original = "def process(data):\n    return data.strip()"
        code, source = executor.execute(bp, original)
        assert source == SOURCE_TEMPLATE
        assert code is not None
        assert "def process" in code

    def test_fuzzy_match(self, executor):
        """模糊匹配：null_checks → null_check（编辑距离1）。"""
        bp = Blueprint(
            target_file="test.py",
            target_function="process",
            operation="add null check",
            logic_description="check input",
            pattern="null_checks",  # 多了个s
        )
        original = "def process(data):\n    return data.strip()"
        code, source = executor.execute(bp, original)
        assert source == SOURCE_TEMPLATE_FUZZY
        assert code is not None

    def test_no_pattern_returns_llm_direct(self, executor):
        """pattern为空字符串时返回llm_direct。"""
        bp = Blueprint(
            target_file="test.py",
            target_function="process",
            operation="fix bug",
            logic_description="fix",
            pattern="",
        )
        original = "def process(data):\n    return data.strip()"
        code, source = executor.execute(bp, original)
        assert code is None
        assert source == SOURCE_LLM_DIRECT

    def test_unknown_pattern_returns_llm_direct(self, executor):
        """完全不匹配的模板名返回llm_direct。"""
        bp = Blueprint(
            target_file="test.py",
            target_function="process",
            operation="fix bug",
            logic_description="fix",
            pattern="completely_unknown_xyz",
        )
        original = "def process(data):\n    return data.strip()"
        code, source = executor.execute(bp, original)
        assert code is None
        assert source == SOURCE_LLM_DIRECT

    def test_syntax_error_original_returns_failed(self, executor):
        """原始代码有语法错误时返回template_failed。"""
        bp = Blueprint(
            target_file="test.py",
            target_function="process",
            operation="add null check",
            logic_description="check",
            pattern="null_check",
        )
        original = "def process(data\n    return data"  # 语法错误
        code, source = executor.execute(bp, original)
        assert code is None
        assert source == SOURCE_TEMPLATE_FAILED

    def test_safe_substitute_missing_vars(self, executor):
        """缺失变量时safe_substitute不报错（保留${var}原样）。"""
        bp = Blueprint(
            target_file="test.py",
            target_function="process",
            operation="add guard",
            logic_description="guard",
            pattern="add_return_guard",
        )
        original = "def process(data):\n    return data.strip()"
        code, source = executor.execute(bp, original)
        # safe_substitute不会因为缺少变量而失败
        assert source in (SOURCE_TEMPLATE, SOURCE_TEMPLATE_FAILED)

    def test_ast_extract_typed_args(self, executor):
        """AST提取带类型注解的参数。"""
        bp = Blueprint(
            target_file="test.py",
            target_function="calc",
            operation="add null check",
            logic_description="check",
            pattern="null_check",
        )
        original = "def calc(x: int, y: float = 0.0):\n    return x + y"
        code, source = executor.execute(bp, original)
        assert source == SOURCE_TEMPLATE
        assert code is not None
        assert "calc" in code

    def test_list_templates(self, executor):
        """list_templates返回排序后的模板名列表。"""
        templates = executor.list_templates()
        assert templates == ["add_return_guard", "null_check", "stub_implement"]

    def test_reload(self, executor, tmp_path):
        """reload后能加载新模板。"""
        # 追加新模板
        skill_path = tmp_path / "builtin" / "bugfix" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        content += '\n```template:new_one\ndef ${function_name}():\n    pass\n```\n'
        skill_path.write_text(content, encoding="utf-8")
        executor.reload()
        assert "new_one" in executor.list_templates()
