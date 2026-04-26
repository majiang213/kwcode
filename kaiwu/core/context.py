"""
TaskContext: shared data structure passed through the expert pipeline.
Each expert reads from and writes to specific fields only.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TaskContext:
    """Immutable-ish context flowing through the pipeline. Each expert owns its output field."""

    # Input (set once at pipeline start)
    user_input: str = ""
    project_root: str = "."
    gate_result: dict = field(default_factory=dict)
    kaiwu_memory: str = ""

    # Locator output (RED-3: independent context, only Locator writes here)
    locator_output: Optional[dict] = None
    # Expected shape: {"relevant_files": [...], "relevant_functions": [...], "edit_locations": [...]}

    # Generator output (RED-3: independent context, only Generator writes here)
    generator_output: Optional[dict] = None
    # Expected shape: {"patches": [{"file": ..., "original": ..., "modified": ...}], "explanation": ...}

    # Verifier output (RED-3: independent context, only Verifier writes here)
    verifier_output: Optional[dict] = None
    # Expected shape: {"passed": bool, "syntax_ok": bool, "tests_passed": int, "tests_total": int, "error_detail": ...}

    # Expert system prompt (injected when routed via expert registry)
    expert_system_prompt: str = ""

    # Retry / search state
    retry_count: int = 0
    search_triggered: bool = False
    search_results: str = ""

    # Collected file contents (populated by Locator for Generator use)
    relevant_code_snippets: dict = field(default_factory=dict)
    # shape: {"path/to/file.py": "code content around target function"}
