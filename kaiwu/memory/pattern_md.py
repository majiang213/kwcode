"""
PATTERN.md memory: high-frequency task patterns (flywheel data source).
Spec §7.2: stored in .kaiwu/PATTERN.md.
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

from kaiwu.core.context import TaskContext

logger = logging.getLogger(__name__)

PATTERN_MD_TEMPLATE = """# 高频任务模式
> 飞轮数据源，自动维护

## 模式统计
| 任务类型 | 次数 | 成功率 | 平均耗时 | 最近触发 |
|---------|------|--------|---------|---------|

## 候选专家触发
"""

# Internal JSON sidecar for structured stats (markdown is for display)
_STATS_FILENAME = ".pattern_stats.json"


def _kaiwu_dir(project_root: str) -> str:
    return os.path.join(project_root, ".kaiwu")


def _md_path(project_root: str) -> str:
    return os.path.join(_kaiwu_dir(project_root), "PATTERN.md")


def _stats_path(project_root: str) -> str:
    return os.path.join(_kaiwu_dir(project_root), _STATS_FILENAME)


def _ensure_dir(project_root: str):
    d = _kaiwu_dir(project_root)
    if not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _load_stats(project_root: str) -> dict:
    """Load structured stats from JSON sidecar."""
    path = _stats_path(project_root)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_stats(project_root: str, stats: dict):
    """Save structured stats to JSON sidecar."""
    _ensure_dir(project_root)
    path = _stats_path(project_root)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to write pattern stats: %s", e)


def _rebuild_markdown(project_root: str, stats: dict):
    """Rebuild PATTERN.md from structured stats."""
    _ensure_dir(project_root)
    lines = [
        "# 高频任务模式",
        "> 飞轮数据源，自动维护",
        "",
        "## 模式统计",
        "| 任务类型 | 次数 | 成功率 | 平均耗时 | 最近触发 |",
        "|---------|------|--------|---------|---------|",
    ]

    # Sort by count descending
    sorted_types = sorted(stats.items(), key=lambda x: x[1].get("count", 0), reverse=True)
    for task_type, data in sorted_types:
        count = data.get("count", 0)
        success = data.get("success", 0)
        rate = f"{success / count * 100:.0f}%" if count > 0 else "0%"
        total_elapsed = data.get("total_elapsed", 0.0)
        avg_elapsed = f"{total_elapsed / count:.1f}s" if count > 0 else "N/A"
        last_trigger = data.get("last_trigger", "N/A")
        lines.append(f"| {task_type} | {count} | {rate} | {avg_elapsed} | {last_trigger} |")

    lines.append("")
    lines.append("## 候选专家触发")

    # Auto-detect candidates: >=5 tasks with 100% success
    for task_type, data in sorted_types:
        count = data.get("count", 0)
        success = data.get("success", 0)
        if count >= 5 and success == count:
            avg_elapsed = data.get("total_elapsed", 0.0) / count
            lines.append(f"- {task_type}: {count}次全部成功，平均{avg_elapsed:.1f}s")

    lines.append("")

    path = _md_path(project_root)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        logger.warning("Failed to write PATTERN.md: %s", e)


# ── Public API ──


def load(project_root: str) -> str:
    """Return pattern summary as injectable context."""
    path = _md_path(project_root)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.warning("Failed to read PATTERN.md: %s", e)
        return ""


def update(project_root: str, ctx: TaskContext, success: bool, elapsed: float = 0.0):
    """Update pattern stats after every task (success or failure)."""
    expert_type = ctx.gate_result.get("expert_type", "unknown")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    stats = _load_stats(project_root)

    if expert_type not in stats:
        stats[expert_type] = {
            "count": 0,
            "success": 0,
            "total_elapsed": 0.0,
            "last_trigger": "",
        }

    entry = stats[expert_type]
    entry["count"] += 1
    if success:
        entry["success"] += 1
    entry["total_elapsed"] += elapsed
    entry["last_trigger"] = now

    _save_stats(project_root, stats)
    _rebuild_markdown(project_root, stats)
    logger.info("Updated PATTERN.md for %s (success=%s)", expert_type, success)


def get_pattern_stats(project_root: str) -> list[dict]:
    """Return structured stats for flywheel consumption."""
    stats = _load_stats(project_root)
    result = []
    for task_type, data in stats.items():
        count = data.get("count", 0)
        success = data.get("success", 0)
        result.append({
            "task_type": task_type,
            "count": count,
            "success_rate": success / count if count > 0 else 0.0,
            "avg_elapsed": data.get("total_elapsed", 0.0) / count if count > 0 else 0.0,
            "last_trigger": data.get("last_trigger", ""),
        })
    return sorted(result, key=lambda x: x["count"], reverse=True)


def show(project_root: str) -> str:
    """Display PATTERN.md content."""
    path = _md_path(project_root)
    if not os.path.exists(path):
        return "PATTERN.md not found. Will be created after first task."
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Failed to read PATTERN.md: {e}"
