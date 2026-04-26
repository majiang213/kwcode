"""
DuckDuckGo HTML scraper：唯一搜索入口（SEARCH-RED-5）。
零 API key，零注册，BeautifulSoup 解析。
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"
DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# 延迟导入 bs4，安装失败时在 search() 里优雅降级
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    BeautifulSoup = None


def search(query: str, max_results: int = 10, timeout: float = 10.0) -> list[dict]:
    """
    搜索 DuckDuckGo，返回 [{url, title, snippet}, ...]。
    失败返回空列表（SEARCH-RED-3）。
    """
    if HAS_BS4:
        return _search_bs4(query, max_results, timeout)
    return _search_regex(query, max_results, timeout)


def _search_bs4(query: str, max_results: int, timeout: float) -> list[dict]:
    """BeautifulSoup 解析 DDG HTML（主路径）。"""
    try:
        resp = httpx.post(
            DDG_URL,
            data={"q": query, "b": ""},
            headers=DDG_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[ddg] request failed: %s", e)
        return []

    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        # lxml 不可用时降级到 html.parser
        soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for item in soup.select(".result"):
        # 标题
        title_tag = item.select_one(".result__a")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # URL — DDG 的链接在 href 里是跳转链接，真实 URL 在 result__url 里
        url = ""
        url_tag = item.select_one(".result__url")
        if url_tag:
            url = url_tag.get_text(strip=True)
            if url and not url.startswith("http"):
                url = "https://" + url

        # 也尝试从 a 标签的 href 提取
        if not url and title_tag and title_tag.get("href"):
            href = title_tag["href"]
            if "uddg=" in href:
                # DDG 跳转链接格式: //duckduckgo.com/l/?uddg=REAL_URL&...
                import urllib.parse
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                url = parsed.get("uddg", [""])[0]
            elif href.startswith("http"):
                url = href

        # Snippet
        snippet_tag = item.select_one(".result__snippet")
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

        if url and (title or snippet):
            results.append({"url": url, "title": title, "snippet": snippet})
            if len(results) >= max_results:
                break

    logger.info("[ddg] query=%r results=%d", query[:50], len(results))
    return results


def _search_regex(query: str, max_results: int, timeout: float) -> list[dict]:
    """Regex 降级路径（bs4 不可用时）。"""
    import re

    try:
        resp = httpx.get(
            DDG_URL,
            params={"q": query},
            headers=DDG_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[ddg-regex] request failed: %s", e)
        return []

    html = resp.text
    results = []

    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    urls = re.findall(r'class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)

    for i in range(min(len(titles), max_results)):
        title = re.sub(r"<[^>]+>", "", titles[i]).strip() if i < len(titles) else ""
        snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
        url = re.sub(r"<[^>]+>", "", urls[i]).strip() if i < len(urls) else ""
        if url and not url.startswith("http"):
            url = "https://" + url
        if url:
            results.append({"url": url, "title": title, "snippet": snippet})

    return results
