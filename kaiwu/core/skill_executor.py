"""
SkillExecutor: 根据Blueprint确定性填充模板，全程不走LLM。
Template.safe_substitute保证不抛异常（缺失变量保留原样）。
"""

import ast
from string import Template
from typing import Optional

from kaiwu.core.blueprint import Blueprint
from kaiwu.core.skill_parser import SkillParser

__all__ = ["SkillExecutor", "SOURCE_TEMPLATE", "SOURCE_TEMPLATE_FUZZY",
           "SOURCE_TEMPLATE_FAILED", "SOURCE_LLM_DIRECT"]

SOURCE_TEMPLATE = "template"
SOURCE_TEMPLATE_FUZZY = "template_fuzzy"
SOURCE_TEMPLATE_FAILED = "template_failed"
SOURCE_LLM_DIRECT = "llm_direct"


class SkillExecutor:
    """根据Blueprint和SKILL.md模板确定性生成代码。不调用LLM。"""

    def __init__(self, builtin_dir: str, project_dir: str = "."):
        self._parser = SkillParser()
        self._builtin_dir = builtin_dir
        self._project_dir = project_dir
        self._templates: dict[str, Template] = {}
        self._reload_templates()

    def _reload_templates(self):
        """重新加载所有模板（SKILL.md更新后调用）。"""
        self._templates = self._parser.load_all_templates(
            self._builtin_dir, self._project_dir
        )

    def execute(self, blueprint: Blueprint,
                original_code: str) -> tuple[Optional[str], str]:
        """
        返回 (生成的代码或None, source标记)。
        source: template / template_fuzzy / template_failed / llm_direct
        """
        if not blueprint.pattern:
            return None, SOURCE_LLM_DIRECT

        # 精确匹配
        tmpl = self._templates.get(blueprint.pattern)
        source = SOURCE_TEMPLATE

        # 模糊匹配（编辑距离<=2）
        if tmpl is None:
            fuzzy = self._fuzzy_match(blueprint.pattern)
            if fuzzy:
                tmpl = self._templates[fuzzy]
                source = SOURCE_TEMPLATE_FUZZY

        if tmpl is None:
            return None, SOURCE_LLM_DIRECT

        # 用AST提取参数
        params = self._extract_params(blueprint, original_code)
        if params is None:
            return None, SOURCE_TEMPLATE_FAILED

        try:
            code = tmpl.safe_substitute(params)
            # 验证生成的代码语法正确
            ast.parse(code)
            return code, source
        except SyntaxError:
            return None, SOURCE_TEMPLATE_FAILED
        except Exception:
            return None, SOURCE_TEMPLATE_FAILED

    def _extract_params(self, blueprint: Blueprint,
                        original_code: str) -> Optional[dict]:
        """
        用AST从原始代码提取模板参数。
        提取失败返回None。
        """
        try:
            tree = ast.parse(original_code)
        except SyntaxError:
            return None

        params = {}

        # 提取函数信息
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params["function_name"] = node.name
                # 参数列表（带类型注解）
                args = []
                for arg in node.args.args:
                    if arg.annotation:
                        args.append(f"{arg.arg}: {ast.unparse(arg.annotation)}")
                    else:
                        args.append(arg.arg)
                params["args"] = ", ".join(args)
                # 函数体（保留缩进，用于${original_body}）
                if node.body:
                    body_start = node.body[0].lineno - 1
                    body_lines = original_code.split('\n')[body_start:]
                    # 去掉第一层缩进，模板负责加回来
                    params["original_body"] = '\n'.join(
                        line[4:] if line.startswith('    ') else line
                        for line in body_lines
                        if line.strip()
                    )
                else:
                    params["original_body"] = "pass"
                break

        if not params:
            return None

        # 加入blueprint的字段
        params["operation"] = blueprint.operation
        params["logic"] = blueprint.logic_description

        # 加入upstream约束（如有）
        if blueprint.constraints:
            params["constraints"] = '\n'.join(blueprint.constraints)

        # 从requires提取常见参数
        for req in blueprint.requires:
            if 'redis' in req.lower():
                params["redis_client"] = "redis_client"

        # 通用占位：check_target / default_return
        params.setdefault("check_target", "value")
        params.setdefault("default_return", "None")

        return params

    def _fuzzy_match(self, pattern: str,
                     max_distance: int = 2) -> Optional[str]:
        """编辑距离模糊匹配模板名。"""
        if not self._templates:
            return None

        best, best_dist = None, max_distance + 1
        for name in self._templates:
            d = self._edit_distance(pattern.lower(), name.lower())
            if d < best_dist:
                best, best_dist = name, d
        return best if best_dist <= max_distance else None

    @staticmethod
    def _edit_distance(a: str, b: str) -> int:
        """Levenshtein编辑距离。"""
        if len(a) > len(b):
            a, b = b, a
        prev = list(range(len(a) + 1))
        for j in range(1, len(b) + 1):
            curr = [j] + [0] * len(a)
            for i in range(1, len(a) + 1):
                if a[i-1] == b[j-1]:
                    curr[i] = prev[i-1]
                else:
                    curr[i] = 1 + min(prev[i], curr[i-1], prev[i-1])
            prev = curr
        return prev[len(a)]

    def list_templates(self) -> list[str]:
        """列出所有可用模板名。"""
        return sorted(self._templates.keys())

    def reload(self):
        """SKILL.md更新后重新加载，用于热更新。"""
        self._reload_templates()
