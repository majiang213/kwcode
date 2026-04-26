"""
Kaiwu 3-layer memory system.

- project_md: PROJECT.md — project structure info
- expert_md: EXPERT.md — successful expert call records
- pattern_md: PATTERN.md — high-frequency task patterns (flywheel)
- kaiwu_md: KaiwuMemory facade (backward-compatible)
"""

from kaiwu.memory.kaiwu_md import KaiwuMemory
from kaiwu.memory import project_md, expert_md, pattern_md

__all__ = ["KaiwuMemory", "project_md", "expert_md", "pattern_md"]
