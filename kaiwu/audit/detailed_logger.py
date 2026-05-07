"""
DetailedLogger: 完整不截断的流水线日志记录器。

每个任务生成一个 JSON 文件，记录：
- LLM 完整 prompt/output（不截断）
- 各节点（Gate/Locator/Generator/Verifier）的输入输出
- 工程机制决策（重试策略、搜索、熔断等）

输出目录：环境变量 KWCODE_DETAIL_LOG_DIR，默认为项目 logs/ 目录。
设为空字符串可禁用。
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 默认日志目录（相对于源码根目录，开发调试用）
_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


def _get_log_dir() -> Optional[Path]:
    """获取详细日志输出目录。返回 None 表示禁用。"""
    env_val = os.environ.get("KWCODE_DETAIL_LOG_DIR")
    if env_val is not None:
        if env_val == "":
            return None  # 显式禁用
        return Path(env_val)
    return _DEFAULT_LOG_DIR


class DetailedLogger:
    """单任务详细日志记录器。非阻塞，所有操作失败静默。"""

    def __init__(self, user_input: str = "", model: str = "unknown"):
        self._enabled = True
        self._log_dir = _get_log_dir()
        if self._log_dir is None:
            self._enabled = False
            return

        self._start_time = time.time()
        self._task_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self._user_input = user_input
        self._model = model
        self._timeline: list[dict] = []
        self._metadata: dict = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_metadata(self, **kwargs):
        """设置任务级元数据（expert_type, difficulty, routing_source 等）。"""
        if not self._enabled:
            return
        self._metadata.update(kwargs)

    def log_llm(self, caller: str, prompt: str, system: str,
                raw_output: str, tokens: Optional[dict] = None,
                elapsed_ms: float = 0, messages: Optional[list] = None):
        """
        记录一次 LLM 调用（完整不截断）。

        Args:
            caller: 调用方标识（gate/generator/verifier/reflection 等）
            prompt: 完整 prompt 文本（非 chat 模式时）
            system: system prompt
            raw_output: LLM 原始输出
            tokens: {"input": N, "output": N}
            elapsed_ms: 调用耗时毫秒
            messages: chat 模式的完整 messages 列表
        """
        if not self._enabled:
            return
        try:
            entry = {
                "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "elapsed_s": round(time.time() - self._start_time, 2),
                "type": "llm_call",
                "caller": caller,
                "input": {},
                "output": raw_output,
                "elapsed_ms": round(elapsed_ms, 1),
                "tokens": tokens or {},
            }
            if messages:
                entry["input"]["messages"] = messages
            else:
                entry["input"]["system"] = system
                entry["input"]["prompt"] = prompt
            self._timeline.append(entry)
        except Exception:
            pass

    def log_node(self, stage: str, input_data: dict, output_data: dict,
                 detail: str = ""):
        """
        记录一个流水线节点的输入输出。

        Args:
            stage: 节点名称（gate/locator/generator/verifier/search 等）
            input_data: 节点接收的输入
            output_data: 节点产出的输出
            detail: 可选的补充说明
        """
        if not self._enabled:
            return
        try:
            entry = {
                "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "elapsed_s": round(time.time() - self._start_time, 2),
                "type": "node_io",
                "stage": stage,
                "input": input_data,
                "output": output_data,
            }
            if detail:
                entry["detail"] = detail
            self._timeline.append(entry)
        except Exception:
            pass

    def log_decision(self, stage: str, decision: str, reason: str = "",
                     context: Optional[dict] = None):
        """
        记录一个工程决策（重试策略选择、熔断、搜索触发等）。

        Args:
            stage: 决策发生的阶段
            decision: 决策内容
            reason: 决策原因
            context: 相关上下文数据
        """
        if not self._enabled:
            return
        try:
            entry = {
                "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "elapsed_s": round(time.time() - self._start_time, 2),
                "type": "decision",
                "stage": stage,
                "decision": decision,
                "reason": reason,
            }
            if context:
                entry["context"] = context
            self._timeline.append(entry)
        except Exception:
            pass

    def write(self, expert_type: str = "unknown", success: bool = False):
        """任务结束时写入日志文件。非阻塞。"""
        if not self._enabled:
            return
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)

            record = {
                "task_id": self._task_id,
                "user_input": self._user_input,
                "model": self._model,
                "expert_type": expert_type,
                "success": success,
                "total_elapsed_s": round(time.time() - self._start_time, 2),
                "timestamp": datetime.now().isoformat(),
                **self._metadata,
                "timeline": self._timeline,
            }

            filename = f"{self._task_id}_{expert_type}.json"
            filepath = self._log_dir / filename
            filepath.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("DetailedLog written: %s", filepath)
        except Exception as e:
            logger.debug("DetailedLogger write failed (non-blocking): %s", e)
