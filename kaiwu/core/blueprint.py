"""
Blueprint: LLM生成的施工图数据结构。
Generator Step1产出Blueprint，Step2用SkillExecutor确定性填充模板。
"""

from dataclasses import dataclass, field

__all__ = ["Blueprint"]


@dataclass
class Blueprint:
    """LLM生成的施工图，描述要做什么、怎么做、用哪个模板。"""
    target_file: str
    target_function: str
    operation: str              # 一句话：做什么
    logic_description: str      # 具体逻辑
    pattern: str                # SKILL.md模板名，空字符串=找不到
    requires: list[str] = field(default_factory=list)    # 需要的import
    constraints: list[str] = field(default_factory=list)  # 来自UpstreamManifest
    raw_llm_output: str = ""   # 保留原始LLM输出，便于debug
