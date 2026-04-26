"""
查询生成器：一次 LLM 调用，生成 2-3 条英文搜索 query。
意图影响 query 风格（追加 github/arxiv/fix 等关键词），不影响搜索引擎选择。
"""

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kaiwu.core.context import TaskContext
    from kaiwu.llm.llama_backend import LLMBackend

logger = logging.getLogger(__name__)

QUERY_GEN_PROMPT = """\
You are a search query generator for a coding agent.
Given the user's coding task and intent, generate 2-3 concise English search queries.

Rules:
- Each query should be a short search engine query (5-12 words)
- Queries should cover different angles of the problem
- {direction}
- Output ONLY a JSON array of strings, no explanation.

User task: {task}
Verifier feedback: {feedback}
"""

# 意图 → query 方向提示
_DIRECTION_MAP = {
    "github": "Include 'github' or 'repository' in at least one query",
    "arxiv": "Include 'arxiv' or 'paper' in at least one query",
    "pypi": "Include 'python package' or 'pip install' in at least one query",
    "bug": "Include 'fix' or 'solution' in at least one query",
    "general": "Focus on practical coding solutions",
}


class QueryGenerator:
    def __init__(self, llm: "LLMBackend"):
        self.llm = llm

    def generate(self, ctx: "TaskContext", intent: str) -> list[str]:
        """生成 2-3 条英文搜索 query。解析失败时回退到 task_summary。"""
        direction = _DIRECTION_MAP.get(intent, _DIRECTION_MAP["general"])

        # 提取 verifier 反馈（如有）
        feedback = ""
        if ctx.verifier_output and isinstance(ctx.verifier_output, dict):
            feedback = ctx.verifier_output.get("error_detail", "") or ""

        prompt = QUERY_GEN_PROMPT.format(
            direction=direction,
            task=ctx.user_input,
            feedback=str(feedback)[:500],  # 截断避免过长
        )

        try:
            raw = self.llm.generate(prompt, max_tokens=256, temperature=0.3)
            queries = self._parse_queries(raw)
            if queries:
                return queries[:3]
        except Exception as e:
            logger.warning("Query generation LLM call failed: %s", e)

        # 回退：直接用 user_input 构造一条 query
        fallback = ctx.user_input.strip()[:100]
        return [fallback] if fallback else ["python coding help"]

    @staticmethod
    def _parse_queries(raw: str) -> list[str]:
        """从 LLM 输出中提取 JSON 数组。容忍 markdown 代码块包裹。"""
        # 去掉 markdown 代码块标记
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return [str(q).strip() for q in result if str(q).strip()]
        except json.JSONDecodeError:
            pass
        # 尝试逐行提取带引号的字符串
        lines = re.findall(r'"([^"]+)"', raw)
        return lines if lines else []
