"""
意图分类器：纯关键词匹配，无 LLM 调用，毫秒级。
将用户输入分类为 github / arxiv / pypi / bug / general。
"""

import re

# 关键词 → 意图映射（优先级从上到下，首次命中即返回）
_INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("bug", ["报错", "error", "bug", "fix", "失败", "异常", "traceback"]),
    ("github", ["开源", "github", "仓库", "repo", "star", "框架推荐"]),
    ("arxiv", ["论文", "paper", "arxiv", "研究", "survey"]),
    ("pypi", ["库", "package", "pip", "安装", "依赖"]),
]

# 预编译正则：每个意图一个 pattern，用 | 连接所有关键词
_INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (intent, re.compile("|".join(re.escape(kw) for kw in keywords), re.IGNORECASE))
    for intent, keywords in _INTENT_KEYWORDS
]


def classify(user_input: str, task_summary: str = "") -> str:
    """
    对用户输入做意图分类。同时检查 user_input 和 task_summary。

    Returns:
        "github" | "arxiv" | "pypi" | "bug" | "general"
    """
    combined = f"{user_input} {task_summary}"
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(combined):
            return intent
    return "general"
