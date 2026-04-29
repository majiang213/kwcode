"""
/plan mode: generate execution plan + risk assessment before running.
P1-RED-2: No file modifications without user confirmation.
P1-RED-5: Risk levels are High/Medium/Low only, no percentages.
"""

import logging
from dataclasses import dataclass, field

from kaiwu.core.context import TaskContext

logger = logging.getLogger(__name__)


@dataclass
class PlanStep:
    index: int
    description: str
    target_files: list[str] = field(default_factory=list)
    target_functions: list[str] = field(default_factory=list)
    risk: str = "Low"           # "High" / "Medium" / "Low"
    risk_reason: str = ""


def estimate_risk(
    step_type: str,
    file_count: int,
    function_count: int,
    cross_module: bool,
    similar_failures: int,
    description_clarity: float,
) -> str:
    """
    Risk assessment based on task characteristics.
    Priority: historical failures > task complexity > description clarity.
    Returns "High" / "Medium" / "Low" (P1-RED-5: no percentages).
    """
    score = 0

    # Historical failures (most important signal)
    if similar_failures >= 3:
        score += 3
    elif similar_failures >= 1:
        score += 1

    # Task complexity
    if file_count > 3:
        score += 2
    elif file_count > 1:
        score += 1

    if function_count > 8:
        score += 2
    elif function_count > 3:
        score += 1

    if cross_module:
        score += 1

    # Description clarity
    if description_clarity < 0.6:
        score += 1

    if score >= 5:
        return "High"
    elif score >= 2:
        return "Medium"
    else:
        return "Low"


class Planner:

    def __init__(self, locator, pattern_md_module):
        self.locator = locator
        self.pattern_md = pattern_md_module

    def generate_plan(self, ctx: TaskContext) -> list[PlanStep]:
        """Generate execution plan without modifying any files (P1-RED-2)."""
        from kaiwu.core.orchestrator import EXPERT_SEQUENCES

        expert_type = ctx.gate_result.get("expert_type", "locator_repair")
        pipeline = ctx.gate_result.get("pipeline") or EXPERT_SEQUENCES.get(
            expert_type, ["generator", "verifier"]
        )

        # Preview: try graph locator for file/function estimates (read-only)
        files, functions = self._preview_locator(ctx)
        cross_module = len(set(f.split("/")[0] for f in files if "/" in f)) > 1

        # Query historical failures
        similar_failures = self.pattern_md.count_similar_failures(
            expert_type=expert_type,
            keywords=ctx.user_input.split()[:5],
            project_root=ctx.project_root,
        )

        # 读取历史Reflexion作为风险提示
        historical_reflections = ""
        try:
            from kaiwu.memory.pattern_md import get_reflections_for_plan
            historical_reflections = get_reflections_for_plan(ctx.project_root, expert_type)
        except Exception:
            pass

        # Overall risk
        risk = estimate_risk(
            step_type=expert_type,
            file_count=len(files),
            function_count=len(functions),
            cross_module=cross_module,
            similar_failures=similar_failures,
            description_clarity=ctx.gate_result.get("confidence", 1.0),
        )

        # Build risk reason
        reasons = []
        if similar_failures >= 1:
            reasons.append(f"历史上类似任务失败{similar_failures}次")
        if len(files) > 3:
            reasons.append(f"涉及{len(files)}个文件")
        if cross_module:
            reasons.append("跨模块修改")
        if ctx.gate_result.get("confidence", 1.0) < 0.6:
            reasons.append("任务描述较模糊")
        risk_reason = "、".join(reasons) if reasons else "任务清晰，风险可控"

        # Generate steps
        steps = []
        for i, step_name in enumerate(pipeline, 1):
            if step_name == "locator":
                steps.append(PlanStep(
                    index=i,
                    description="定位相关文件和函数",
                    target_files=files,
                    target_functions=functions[:5],
                    risk="Low",
                    risk_reason="只读操作，不修改文件",
                ))
            elif step_name == "generator":
                steps.append(PlanStep(
                    index=i,
                    description="生成修改方案",
                    target_files=files,
                    target_functions=functions[:5],
                    risk=risk,
                    risk_reason=risk_reason,
                ))
            elif step_name == "verifier":
                steps.append(PlanStep(
                    index=i,
                    description="验证修改结果（语法检查 + pytest）",
                    target_files=files,
                    target_functions=[],
                    risk="Low",
                    risk_reason="验证不修改文件",
                ))
            elif step_name == "office":
                steps.append(PlanStep(
                    index=i,
                    description="生成Office文档",
                    target_files=[],
                    target_functions=[],
                    risk="Low",
                    risk_reason="生成新文件，不修改已有文件",
                ))
            elif step_name == "chat":
                steps.append(PlanStep(
                    index=i,
                    description="回复问题",
                    target_files=[],
                    target_functions=[],
                    risk="Low",
                    risk_reason="不修改文件",
                ))

        # 注入历史Reflexion到plan展示
        self._historical_reflections = historical_reflections

        return steps

    def print_plan(self, steps: list[PlanStep], console):
        """Render plan to terminal."""
        RISK_COLOR = {"High": "red", "Medium": "yellow", "Low": "green"}
        RISK_ICON = {"High": "⚠", "Medium": "△", "Low": "✓"}

        console.print("\n  [bold]执行计划[/bold]\n")

        for step in steps:
            color = RISK_COLOR[step.risk]
            icon = RISK_ICON[step.risk]

            console.print(
                f"  步骤{step.index}：{step.description}  "
                f"[{color}]{icon} {step.risk}风险[/{color}]"
            )

            if step.target_files:
                files_str = "、".join(step.target_files[:3])
                if len(step.target_files) > 3:
                    files_str += f" 等{len(step.target_files)}个文件"
                console.print(f"    文件：{files_str}")

            if step.target_functions:
                funcs_str = "、".join(step.target_functions[:3])
                console.print(f"    函数：{funcs_str}")

            console.print(f"    [dim]{step.risk_reason}[/dim]")
            console.print()

        # Overall risk summary
        max_risk = max(steps, key=lambda s: {"Low": 0, "Medium": 1, "High": 2}[s.risk])
        if max_risk.risk == "High":
            console.print("  [red]⚠ 此任务包含高风险步骤，建议先备份或拆分执行[/red]")
        elif max_risk.risk == "Medium":
            console.print("  [yellow]△ 此任务有一定风险，请确认修改范围[/yellow]")

        # 显示历史Reflexion风险提示
        reflections = getattr(self, "_historical_reflections", "")
        if reflections:
            console.print("  [dim]── 历史经验 ──[/dim]")
            for line in reflections.splitlines():
                if line.strip():
                    console.print(f"  [dim]{line}[/dim]")

    def _preview_locator(self, ctx: TaskContext) -> tuple[list[str], list[str]]:
        """Read-only preview of locator results for planning."""
        try:
            if hasattr(self.locator, '_retriever') and self.locator._retriever:
                self.locator._ensure_graph(ctx.project_root)
                results = self.locator._retriever.retrieve(
                    query=ctx.user_input, top_k_bm25=10, graph_hops=1, max_results=5,
                )
                if results:
                    results = [r for r in results if r.get("file_path") and r.get("name")]
                    files = list(dict.fromkeys(r["file_path"] for r in results))
                    funcs = [r["name"] for r in results[:5]]
                    return files, funcs
        except Exception as e:
            logger.debug("[planner] preview failed: %s", e)
        return [], []
