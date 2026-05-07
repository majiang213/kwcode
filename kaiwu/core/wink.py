"""
Wink 自修复监控：轨迹监控 + 偏离检测 + 课程纠正。

轻量异步观察 agent 执行，检测三类问题行为：
- Specification Drift：偏离用户原始意图（scope creep）
- Reasoning Problems：同类错误反复（原地打转）
- Tool Call Failures：patch 持续失败
- Progress Stall：重试未提升通过率（免疫机制）

理论来源：
- Wink: Recovering from Misbehaviors in Coding Agents（arXiv:2602.17037）
- CodeScout 问题陈述增强（arXiv:2603.05744）
"""

import logging
from typing import Optional

from kaiwu.core.event_bus import EventBus

logger = logging.getLogger(__name__)


class WinkMonitor:
    """
    轻量轨迹监控器：不阻塞主流程，纯观察 + 定期检查。
    检测到偏离时返回纠正 hint，由 orchestrator 注入 retry prompt。
    """

    DRIFT_PATTERNS = [
        # Specification Drift：任务范围过大
        {
            "name": "scope_creep",
            "detect": lambda ctx: (
                ctx.locator_output and
                len(ctx.locator_output.get("relevant_files", [])) > 5 and
                ctx.gate_result.get("difficulty") == "easy"
            ),
            "hint": "任务范围过大，只修改用户明确指定的文件，不要扩散到其他文件",
        },
        # Reasoning Problems：同类错误反复
        {
            "name": "repetitive_fix",
            "detect": lambda ctx: (
                hasattr(ctx, '_error_type_streak') and
                ctx._error_type_streak.get("count", 0) >= 2
            ),
            "hint": "你已经用同样的方式修改了 {count} 次，换一个完全不同的思路",
        },
        # Tool Call Failures：patch 持续失败
        {
            "name": "patch_miss",
            "detect": lambda ctx: (
                ctx.verifier_output and
                ctx.verifier_output.get("error_type") == "patch_apply" and
                ctx.retry_count >= 1
            ),
            "hint": "patch 未命中，文件内容可能已变化，请重新读取文件再生成 patch",
        },
        # Generator 输出为空（模型拒绝或无法理解）
        {
            "name": "empty_output",
            "detect": lambda ctx: (
                ctx.generator_output and
                not ctx.generator_output.get("patches") and
                ctx.retry_count >= 1
            ),
            "hint": "Generator 未产出有效 patch，尝试简化任务描述或缩小修改范围",
        },
        # 免疫机制：重试未提升通过率（结疤，不改变结构）
        {
            "name": "tests_no_progress",
            "detect": lambda ctx: (
                ctx.retry_count >= 2 and
                ctx.verifier_output and
                hasattr(ctx, '_prev_tests_passed') and
                ctx.verifier_output.get("tests_passed", 0) <= getattr(ctx, '_prev_tests_passed', 0)
            ),
            "hint": "连续重试未提升通过率，尝试完全不同的实现方式，不要在同一个方向上继续",
        },
    ]

    def check(self, ctx, bus: Optional[EventBus] = None) -> Optional[str]:
        """
        检查当前 context 是否有偏离，返回纠正 hint 或 None。
        非阻塞，任何异常静默忽略。
        """
        # 记录本次tests_passed供下次比较（免疫机制的记忆）
        if ctx.verifier_output:
            ctx._prev_tests_passed = ctx.verifier_output.get("tests_passed", 0)

        for pattern in self.DRIFT_PATTERNS:
            try:
                if pattern["detect"](ctx):
                    # 格式化 hint
                    hint = pattern["hint"]
                    if "{count}" in hint and hasattr(ctx, "_error_type_streak"):
                        hint = hint.format(count=ctx._error_type_streak.get("count", 0))

                    if bus:
                        bus.emit("wink_intervene", {
                            "pattern": pattern["name"],
                            "msg": f"检测到 {pattern['name']}，注入纠正"
                        })

                    logger.info("[wink] detected %s, injecting hint", pattern["name"])
                    return hint
            except Exception:
                continue
        return None
