"""
BlueprintCollector: 收集成功任务的施工图，双写项目级+全局。
全局数据是bench训练数据的来源。
只记录成功的任务，失败的丢弃（不污染训练数据）。
"""

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from kaiwu.core.blueprint import Blueprint

__all__ = ["BlueprintCollector"]


class BlueprintCollector:
    """收集成功任务的施工图，双写：项目级+全局。"""

    PROJECT_FILE = ".kaiwu/blueprints.jsonl"
    GLOBAL_FILE = Path.home() / ".kaiwu" / "blueprints.jsonl"

    def record(self, blueprint: Optional[Blueprint],
               final_code: str, success: bool,
               source: str, project_root: str = "."):
        """
        只记录成功的任务，失败的丢弃。
        blueprint为None时跳过。
        """
        if not success or not blueprint or not final_code.strip():
            return

        entry = {
            "operation": blueprint.operation,
            "logic": blueprint.logic_description,
            "pattern": blueprint.pattern,
            "target_function": blueprint.target_function,
            "requires": blueprint.requires,
            "final_code": final_code,
            "source": source,
            "timestamp": time.time(),
        }

        self._append(Path(project_root) / self.PROJECT_FILE, entry)
        self._append(self.GLOBAL_FILE, entry)

    def _append(self, path: Path, entry: dict):
        """追加一行JSONL，失败静默。"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 非阻塞，失败静默

    def get_llm_direct_candidates(self, min_count: int = 3) -> list[dict]:
        """
        找出llm_direct来源里高频operation模式，
        作为新模板候选（供kwcode skill promote使用）。
        """
        if not self.GLOBAL_FILE.exists():
            return []

        counter: Counter = Counter()
        entries: dict[str, dict] = {}

        try:
            with open(self.GLOBAL_FILE, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        if e.get("source") == "llm_direct":
                            key = e.get("operation", "")
                            if key:
                                counter[key] += 1
                                entries[key] = e
                    except (json.JSONDecodeError, KeyError):
                        pass
        except Exception:
            return []

        return [
            entries[op] for op, count in counter.most_common()
            if count >= min_count
        ]
