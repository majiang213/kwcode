"""
AST-based usage finder: deterministically locates all call sites of given functions.
No LLM involved — pure static analysis.
"""

import ast
import glob
import os
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def find_all_usages(project_root: str, func_names: list) -> Dict[str, List[str]]:
    """
    Find all call sites of the given function names across the project.
    Returns {func_name: ["relative/path.py:lineno: code_snippet"]}

    Only searches .py files, skips test files and __pycache__.
    """
    if not func_names:
        return {}

    # Normalize: extract bare function name from "Class.method" format
    lookup = {}
    for name in func_names:
        bare = name.split(".")[-1] if "." in name else name
        lookup[bare] = name

    result = {name: [] for name in func_names}

    py_files = glob.glob(os.path.join(project_root, "**", "*.py"), recursive=True)

    for py_file in py_files:
        rel = os.path.relpath(py_file, project_root).replace("\\", "/")

        # Skip test files and cache
        if "__pycache__" in rel:
            continue
        basename = os.path.basename(py_file).lower()
        if "test" in basename:
            continue

        try:
            with open(py_file, encoding="utf-8", errors="ignore") as f:
                src = f.read()
            tree = ast.parse(src)
            lines = src.split("\n")
        except Exception:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            called = None
            if isinstance(node.func, ast.Attribute):
                called = node.func.attr
            elif isinstance(node.func, ast.Name):
                called = node.func.id

            if called and called in lookup:
                original_name = lookup[called]
                lineno = getattr(node, "lineno", 0)
                snippet = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
                entry = f"{rel}:{lineno}: {snippet[:80]}"
                if entry not in result[original_name]:
                    result[original_name].append(entry)

    # Log summary
    total = sum(len(v) for v in result.values())
    if total:
        logger.debug("[usage_finder] Found %d call sites for %d functions", total, len(func_names))

    return result


def format_usages_for_prompt(usages: Dict[str, List[str]], max_per_func: int = 5) -> str:
    """Format usage results into a prompt-injectable string."""
    parts = []
    for func_name, sites in usages.items():
        if not sites:
            continue
        truncated = sites[:max_per_func]
        lines = "\n".join(f"  {s}" for s in truncated)
        suffix = f"\n  ... 还有{len(sites) - max_per_func}处" if len(sites) > max_per_func else ""
        parts.append(f"### {func_name}() 的调用点\n{lines}{suffix}")

    if not parts:
        return ""
    return "## 调用关系（修改函数签名时必须同步更新这些调用点）\n" + "\n\n".join(parts)
