"""
Generator expert: generates code patches based on Locator output.
RED-2: Deterministic pipeline, generates multiple candidates at fixed temperatures.
RED-3: Independent context window, only sees Locator output + relevant snippets.

Key design: original is read directly from file (never LLM-generated),
LLM only produces the modified version. This guarantees apply_patch exact match.
"""

import json
import logging
import re
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

GENERATOR_PROMPT = """你是代码修复/生成专家。根据任务描述，修改下面的函数代码。

任务描述：{task_description}

需要修改的原始代码（来自 {file_path}）：
```
{original_code}
```

{search_context}

请只输出修改后的完整函数代码。要求：
1. 保持原始缩进风格
2. 只修改必要的部分
3. 输出完整的函数（从def开始到函数结束）
4. 不要用markdown代码块包裹
5. 不要解释，只输出代码"""

GENERATOR_NEWFILE_PROMPT = """你是代码生成专家。根据任务描述生成代码。

任务描述：{task_description}

相关代码上下文：
{code_snippets}

{search_context}

请生成需要的代码。要求：
1. 只输出代码，不要解释
2. 不要用markdown代码块包裹"""

GENERATOR_TEST_PROMPT = """你是测试生成专家。为下面的代码生成 pytest 单元测试。

源代码（来自 {source_file}）：
```
{source_code}
```

任务描述：{task_description}

{search_context}

请生成完整的 pytest 测试文件。要求：
1. 在文件开头 import 被测模块（使用相对路径或 sys.path）
2. 每个函数至少2个测试用例（正常+边界）
3. 使用 assert 语句
4. 只输出代码，不要解释
5. 不要用markdown代码块包裹"""


class GeneratorExpert:
    """Generates code patches. Original is read from file, LLM only generates modified."""

    def __init__(self, llm: LLMBackend, tool_executor: ToolExecutor = None, num_candidates: int = 3):
        self.llm = llm
        self.tools = tool_executor
        self.num_candidates = num_candidates
        self.temperatures = [0.0, 0.3, 0.6][:num_candidates]

    def run(self, ctx: TaskContext) -> Optional[dict]:
        """
        Generate patches. For each target function:
        1. Read original code directly from file (100% accurate)
        2. Ask LLM to generate only the modified version
        3. Package as {file, original, modified} patch
        """
        locator = ctx.locator_output or {}
        files = locator.get("relevant_files", [])
        funcs = locator.get("relevant_functions", [])

        if not files:
            # No locator output — pure codegen task
            return self._run_codegen(ctx)

        # Detect test generation tasks — need to CREATE test file, not modify source
        if self._is_test_generation_task(ctx):
            return self._run_test_generation(ctx, files)

        # For each file+function pair, extract original and generate modified
        # Deduplicate: only patch each (file, function) once
        patches = []
        explanation_parts = []
        seen = set()  # (file, func) pairs already processed

        for fpath in files[:3]:  # Cap at 3 files
            # Skip test files — we only modify source code
            if "test" in fpath.lower():
                continue

            # Read the actual file content
            if self.tools:
                content = self.tools.read_file(fpath)
            else:
                content = ctx.relevant_code_snippets.get(fpath, "")
            if not content or content.startswith("[ERROR]"):
                continue

            # Find target functions in this file (deduplicated)
            file_funcs = list(dict.fromkeys(
                f for f in funcs if self._func_in_file(f, content)
            ))
            if not file_funcs:
                snippet = ctx.relevant_code_snippets.get(fpath, "")
                if snippet:
                    file_funcs = ["_whole_snippet_"]

            for func_name in file_funcs[:2]:  # Cap at 2 functions per file
                key = (fpath, func_name)
                if key in seen:
                    continue
                seen.add(key)
                if func_name == "_whole_snippet_":
                    original = ctx.relevant_code_snippets.get(fpath, content[:2000])
                else:
                    # Extract the exact function text from file
                    original = self._extract_function(content, func_name)
                    if not original:
                        logger.warning("Could not extract function %s from %s", func_name, fpath)
                        continue

                # Ask LLM to generate only the modified version
                modified = self._generate_modified(
                    ctx, fpath, original, ctx.user_input
                )
                if not modified:
                    continue

                # Verify original exists in file (should always be true since we read it)
                if original not in content:
                    logger.error("Extracted original not found in file — this should not happen")
                    continue

                patches.append({
                    "file": fpath,
                    "original": original,
                    "modified": modified,
                })
                explanation_parts.append(f"{fpath}:{func_name}")

        if not patches:
            logger.warning("Generator: no patches produced")
            return None

        result = {
            "patches": patches,
            "explanation": f"Modified: {', '.join(explanation_parts)}",
        }
        ctx.generator_output = result
        return result

    def _generate_modified(self, ctx: TaskContext, fpath: str, original: str, task_desc: str) -> Optional[str]:
        """Ask LLM to generate modified code. Try multiple temperatures."""
        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料：\n{ctx.search_results}"

        prompt = GENERATOR_PROMPT.format(
            task_description=task_desc,
            file_path=fpath,
            original_code=original,
            search_context=search_ctx,
        )

        for temp in self.temperatures:
            raw = self.llm.generate(prompt=prompt, max_tokens=2048, temperature=temp)
            modified = self._clean_code_output(raw)
            if modified and modified != original:
                return modified

        logger.warning("Generator: all candidates identical to original or empty")
        return None

    def _run_codegen(self, ctx: TaskContext) -> Optional[dict]:
        """Pure code generation (no existing file to patch)."""
        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料：\n{ctx.search_results}"

        snippets_text = ""
        for fpath, snippet in ctx.relevant_code_snippets.items():
            snippets_text += f"\n--- {fpath} ---\n{snippet}\n"

        prompt = GENERATOR_NEWFILE_PROMPT.format(
            task_description=ctx.user_input,
            code_snippets=snippets_text[:3000] if snippets_text else "(无上下文)",
            search_context=search_ctx,
        )

        raw = self.llm.generate(prompt=prompt, max_tokens=2048, temperature=0.0)
        code = self._clean_code_output(raw)
        if not code:
            return None

        result = {
            "patches": [{"file": "new_code.py", "original": "", "modified": code}],
            "explanation": "Generated new code",
        }
        ctx.generator_output = result
        return result

    def _is_test_generation_task(self, ctx: TaskContext) -> bool:
        """Detect if the task is about generating tests (not modifying source)."""
        keywords = ["生成测试", "写测试", "单元测试", "test", "pytest", "测试用例", "添加测试"]
        task_lower = ctx.user_input.lower()
        gate_type = ctx.gate_result.get("expert_type", "")
        expert_name = ctx.gate_result.get("expert_name", "")
        if expert_name == "TestGenExpert":
            return True
        if gate_type == "codegen" and any(kw in task_lower for kw in keywords):
            return True
        return any(kw in task_lower for kw in keywords[:3])  # Strong Chinese signals

    def _run_test_generation(self, ctx: TaskContext, source_files: list[str]) -> Optional[dict]:
        """Generate a new test file for the given source files."""
        import os

        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料：\n{ctx.search_results}"

        # Read source files to provide as context
        source_code_parts = []
        primary_source = None
        for fpath in source_files[:3]:
            if "test" in fpath.lower():
                continue
            content = self.tools.read_file(fpath) if self.tools else ""
            if content and not content.startswith("[ERROR]"):
                source_code_parts.append(f"# {fpath}\n{content}")
                if primary_source is None:
                    primary_source = fpath

        if not source_code_parts:
            return self._run_codegen(ctx)

        source_code = "\n\n".join(source_code_parts)

        prompt = GENERATOR_TEST_PROMPT.format(
            source_file=primary_source or "source",
            source_code=source_code[:4000],
            task_description=ctx.user_input,
            search_context=search_ctx,
        )

        raw = self.llm.generate(prompt=prompt, max_tokens=2048, temperature=0.0)
        code = self._clean_code_output(raw)
        if not code:
            return None

        # Determine test file path
        test_dir = os.path.join(ctx.project_root, "tests")
        if primary_source:
            base = os.path.splitext(os.path.basename(primary_source))[0]
            test_file = os.path.join("tests", f"test_{base}.py")
        else:
            test_file = os.path.join("tests", "test_generated.py")

        # Ensure tests/ dir and __init__.py exist
        if self.tools:
            abs_test_dir = os.path.join(ctx.project_root, "tests")
            os.makedirs(abs_test_dir, exist_ok=True)
            init_path = os.path.join(abs_test_dir, "__init__.py")
            if not os.path.exists(init_path):
                with open(init_path, "w", encoding="utf-8") as f:
                    pass

        result = {
            "patches": [{"file": test_file, "original": "", "modified": code}],
            "explanation": f"Generated test file for {primary_source}",
        }
        ctx.generator_output = result
        return result

    @staticmethod
    def _extract_function(content: str, func_name: str) -> Optional[str]:
        """Extract a complete function/method from file content by name."""
        lines = content.split("\n")
        start_idx = -1
        indent_level = -1

        for i, line in enumerate(lines):
            # Match def func_name or class func_name
            stripped = line.lstrip()
            if stripped.startswith(f"def {func_name}") or stripped.startswith(f"class {func_name}"):
                start_idx = i
                indent_level = len(line) - len(stripped)
                break

        if start_idx == -1:
            return None

        # Find the end of the function (next line at same or lower indent level)
        end_idx = start_idx + 1
        while end_idx < len(lines):
            line = lines[end_idx]
            if line.strip() == "":
                end_idx += 1
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent_level and line.strip():
                break
            end_idx += 1

        # Include trailing blank lines that are part of the function block
        while end_idx > start_idx + 1 and lines[end_idx - 1].strip() == "":
            end_idx -= 1

        return "\n".join(lines[start_idx:end_idx])

    @staticmethod
    def _func_in_file(func_name: str, content: str) -> bool:
        """Check if a function/class definition exists in content."""
        return f"def {func_name}" in content or f"class {func_name}" in content

    @staticmethod
    def _clean_code_output(raw: str) -> str:
        """Strip markdown code blocks and extra whitespace from LLM output."""
        text = raw.strip()
        # Remove markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return text.strip()
