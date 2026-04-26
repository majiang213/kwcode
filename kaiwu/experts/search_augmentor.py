"""
SearchAugmentor expert: 6 步搜索流水线。
IntentClassifier → QueryGenerator → DuckDuckGo → QualityFilter → ContentFetcher → ContextCompressor

红线：
  SEARCH-RED-1: 零外部 API key
  SEARCH-RED-3: 失败不中断主流程，返回空字符串
  SEARCH-RED-4: 总耗时 ≤15s
  SEARCH-RED-5: 唯一搜索入口 DuckDuckGo
"""

import logging
import random
import time

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.search.intent_classifier import classify as classify_intent
from kaiwu.search.query_generator import QueryGenerator
from kaiwu.search.duckduckgo import search as ddg_search
from kaiwu.search.quality_filter import filter_results
from kaiwu.search.content_fetcher import ContentFetcher
from kaiwu.search.context_compressor import ContextCompressor

logger = logging.getLogger(__name__)

MAX_SEARCH_SECONDS = 15  # SEARCH-RED-4


class SearchAugmentorExpert:
    """6 步搜索增强流水线。对外接口不变：search(ctx) -> str。"""

    def __init__(self, llm: LLMBackend):
        self.query_gen = QueryGenerator(llm)
        self.fetcher = ContentFetcher()
        self.compressor = ContextCompressor(llm)

    def search(self, ctx: TaskContext) -> str:
        """
        完整搜索流水线。任何异常返回空字符串（SEARCH-RED-3）。
        总耗时超 15s 提前返回已有内容（SEARCH-RED-4）。
        """
        t0 = time.time()
        try:
            # ① 意图分类（纯关键词，毫秒级）
            intent = classify_intent(
                ctx.user_input,
                ctx.gate_result.get("task_summary", ""),
            )
            logger.info("[search] intent=%s", intent)

            # ② 生成 query（一次 LLM 调用）
            queries = self.query_gen.generate(ctx, intent)
            if not queries:
                queries = [ctx.user_input[:80]]
            logger.info("[search] queries=%s", queries)

            if self._overtime(t0):
                return ""

            # ③ DuckDuckGo 搜索（带重试）
            all_results = []
            for q in queries[:3]:
                if self._overtime(t0):
                    break
                results = self._search_with_retry(q)
                all_results.extend(results)

            if not all_results:
                logger.warning("[search] no results from DDG")
                return ""

            # ④ 质量过滤
            filtered = filter_results(all_results, max_fetch=3)
            urls = [r["url"] for r in filtered if r.get("url")]
            logger.info("[search] filtered urls=%s", urls)

            if not urls:
                # 没有可 fetch 的 URL，用 snippet 兜底
                snippets = [r.get("snippet", "") for r in all_results[:5]]
                return self.compressor.compress(ctx.user_input, snippets)

            if self._overtime(t0):
                # 超时但有 snippet，用 snippet 兜底
                snippets = [r.get("snippet", "") for r in filtered]
                return "\n".join(s for s in snippets if s)[:400]

            # ⑤ 正文提取
            remaining = max(2.0, MAX_SEARCH_SECONDS - (time.time() - t0))
            contents = self.fetcher.fetch_many(urls, timeout=remaining)
            logger.info("[search] fetched %d pages", sum(1 for c in contents if c))

            # 如果正文提取全部失败，用 snippet 兜底
            if not any(contents):
                contents = [r.get("snippet", "") for r in filtered]

            if self._overtime(t0):
                # 超时，直接拼接已有内容
                return "\n\n".join(c for c in contents if c)[:400]

            # ⑥ 压缩
            compressed = self.compressor.compress(ctx.user_input, contents)
            elapsed = time.time() - t0
            logger.info("[search] done %.1fs len=%d", elapsed, len(compressed))
            return compressed

        except Exception as e:
            logger.error("[search] pipeline error: %s", e)
            return ""  # SEARCH-RED-3

    @staticmethod
    def _search_with_retry(query: str, max_retries: int = 2) -> list[dict]:
        """DDG 搜索带重试（SEARCH-FLEX-2）。"""
        for attempt in range(max_retries + 1):
            results = ddg_search(query)
            if results:
                return results
            if attempt < max_retries:
                time.sleep(random.uniform(1.0, 3.0))
        return []

    @staticmethod
    def _overtime(t0: float) -> bool:
        """检查是否超过 15s 时间预算。"""
        if time.time() - t0 > MAX_SEARCH_SECONDS:
            logger.warning("[search] overtime, returning early")
            return True
        return False
