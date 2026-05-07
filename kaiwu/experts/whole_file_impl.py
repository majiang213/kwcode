"""
WholeFileImplExpert: 处理存根实现任务。
实现文件中所有未实现的函数（pass存根），而不是只改1-2个。
LLM只做代码生成，不做任何决策。
"""

import ast
import logging
import os
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.core.gap_detector import GapType
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.tools.executor import ToolExecutor

__all__ = ["WholeFileImplExpert"]

logger = logging.getLogger(__name__)

WHOLE_FILE_SYSTEM = """你是代码实现专家。你的任务是实现文件中所有未实现的函数。
规则：
- 输出完整的文件内容，包含所有已实现的函数
- 每个函数都必须有真实的实现，不能保留pass
- 保持原有的类结构和方法签名不变
- 纯代码输出，无markdown代码块标记，无解释文字
- 不添加额外的import（除非实现逻辑必需）
- 不修改已有的非存根函数"""


class WholeFileImplExpert:
    """
    专门处理存根实现任务。
    与Generator的区别：不是patch单个函数，而是实现整个文件。
    """

    def __init__(self, llm: LLMBackend, tool_executor: ToolExecutor):
        self.llm = llm
        self.tools = tool_executor

    def can_handle(self, ctx: TaskContext) -> tuple[bool, float]:
        """确定性判断：gap为NOT_IMPLEMENTED或STUB_RETURNS_NONE时处理。"""
        gap = ctx.gap
        if gap and hasattr(gap, 'gap_type'):
            if gap.gap_type in (GapType.NOT_IMPLEMENTED, GapType.STUB_RETURNS_NONE):
                return True, gap.confidence
        return False, 0.0

    def run(self, ctx: TaskContext) -> Optional[dict]:
        """实现所有存根函数，返回whole_file模式的patches。"""
        # 确定要处理的文件
        files = self._get_target_files(ctx)
        if not files:
            logger.debug("[whole_file_impl] 没有找到目标文件")
            return None

        patches = []
        for fpath in files:
            content = self.tools.read_file(fpath)
            if content.startswith("[ERROR]"):
                continue

            # 用AST找到所有pass存根
            stub_functions = self._find_all_stubs(content)
            if not stub_functions:
                continue

            # 构建prompt
            prompt = self._build_whole_file_prompt(
                fpath=fpath,
                content=content,
                stub_functions=stub_functions,
                task_desc=ctx.user_input,
                test_output=getattr(ctx, 'initial_test_failure', '') or '',
                upstream_constraints=ctx.upstream_constraints,
            )

            # LLM生成完整实现
            think_config = ctx.think_config or {}
            max_tokens = 4096
            if think_config.get("think") and think_config.get("budget", 0) > 0:
                max_tokens += think_config["budget"]

            raw = self.llm.generate(
                prompt=prompt,
                system=WHOLE_FILE_SYSTEM,
                max_tokens=max_tokens,
                temperature=0.0,
            )

            implemented = self._clean_code_output(raw)
            if not implemented:
                continue

            # 基本验证：确保输出是有效Python
            if fpath.endswith('.py'):
                try:
                    ast.parse(implemented)
                except SyntaxError:
                    logger.warning("[whole_file_impl] 生成的代码有语法错误: %s", fpath)
                    continue

            patches.append({
                "file": fpath,
                "original": "",
                "modified": implemented,
                "write_mode": "whole_file",
            })

        if not patches:
            return None

        return {
            "patches": patches,
            "explanation": f"实现了{len(patches)}个文件的存根函数",
        }

    def _get_target_files(self, ctx: TaskContext) -> list[str]:
        """从ctx获取目标文件列表。"""
        files = []

        # 优先从locator_output获取
        if ctx.locator_output:
            files = ctx.locator_output.get("relevant_files", [])

        # 从gap信息获取
        if not files and ctx.gap and hasattr(ctx.gap, 'files'):
            files = ctx.gap.files

        # 过滤：只保留存在的非测试文件
        result = []
        for f in files:
            if not os.path.isabs(f):
                f = os.path.join(ctx.project_root, f)
            if os.path.exists(f) and 'test' not in os.path.basename(f).lower():
                result.append(f)

        return result[:3]  # 最多处理3个文件

    def _find_all_stubs(self, content: str) -> list[str]:
        """AST提取所有pass/raise NotImplementedError函数名。"""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        stubs = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # 检查函数体是否是存根
            if self._is_stub_body(node.body):
                stubs.append(node.name)

        return stubs

    def _is_stub_body(self, body: list) -> bool:
        """判断函数体是否是存根（pass / raise NotImplementedError / ... / return None）。"""
        if len(body) == 0:
            return True
        if len(body) > 2:
            return False

        # 单语句情况
        if len(body) == 1:
            stmt = body[0]
            # pass
            if isinstance(stmt, ast.Pass):
                return True
            # ...（Ellipsis）
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                if stmt.value.value is ...:
                    return True
            # raise NotImplementedError
            if isinstance(stmt, ast.Raise):
                return True
            # return None
            if isinstance(stmt, ast.Return) and stmt.value is None:
                return True

        # docstring + pass/raise
        if len(body) == 2:
            first = body[0]
            second = body[1]
            is_docstring = (isinstance(first, ast.Expr) and
                           isinstance(first.value, ast.Constant) and
                           isinstance(first.value.value, str))
            if is_docstring:
                if isinstance(second, ast.Pass):
                    return True
                if isinstance(second, ast.Raise):
                    return True

        return False

    def _build_whole_file_prompt(self, fpath: str, content: str,
                                  stub_functions: list[str], task_desc: str,
                                  test_output: str, upstream_constraints: str) -> str:
        stubs_list = "\n".join(f"- {f}()" for f in stub_functions)
        test_info = f"\n\n测试失败信息（用于理解期望行为）：\n{test_output[:800]}" if test_output else ""
        constraints = f"\n\n跨文件约束（必须遵守的接口契约）：\n{upstream_constraints}" if upstream_constraints else ""

        return f"""实现文件 {os.path.basename(fpath)} 中所有未实现的函数。

任务描述：{task_desc}

文件当前内容：
{content}

需要实现的函数（所有都要实现，不能只实现其中几个）：
{stubs_list}
{test_info}
{constraints}

要求：
- 输出完整的文件内容（从第一行到最后一行）
- 每个函数都必须有真实的实现逻辑
- 保持原有的import、类结构和方法签名不变
- 根据测试失败信息推断期望的行为"""

    def _clean_code_output(self, raw: str) -> str:
        """清理LLM输出：去掉markdown代码块标记等。"""
        if not raw:
            return ""

        text = raw.strip()

        # 去掉markdown代码块
        if text.startswith("```"):
            lines = text.split("\n")
            # 去掉第一行 ```python 和最后一行 ```
            start = 1
            end = len(lines)
            if lines[-1].strip() == "```":
                end = -1
            text = "\n".join(lines[start:end])

        return text.strip()
