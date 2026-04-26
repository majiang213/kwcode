"""
Expert YAML loader and validator.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {"name", "version", "type", "trigger_keywords", "trigger_min_confidence", "system_prompt", "pipeline"}
VALID_LIFECYCLES = {"new", "mature", "declining", "archived"}
VALID_PIPELINE_STEPS = {"locator", "generator", "verifier"}


class ExpertLoader:
    """Load expert YAML files."""

    @staticmethod
    def load_yaml(path: str) -> dict:
        """Load and validate a single expert YAML file."""
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid YAML structure in {path}")

        valid, err = ExpertLoader.validate(data)
        if not valid:
            raise ValueError(f"Validation failed for {path}: {err}")

        # Ensure defaults
        data.setdefault("lifecycle", "new")
        data.setdefault("performance", {"success_rate": 0.0, "avg_latency_s": 0, "task_count": 0})
        data["_source"] = path
        return data

    @staticmethod
    def load_directory(dir_path: str) -> list[dict]:
        """Load all YAML files from a directory."""
        experts = []
        if not os.path.isdir(dir_path):
            logger.debug("Expert directory not found: %s", dir_path)
            return experts

        for fname in sorted(os.listdir(dir_path)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            fpath = os.path.join(dir_path, fname)
            try:
                expert = ExpertLoader.load_yaml(fpath)
                experts.append(expert)
                logger.debug("Loaded expert: %s from %s", expert["name"], fname)
            except Exception as e:
                logger.warning("Failed to load expert %s: %s", fname, e)
        return experts

    @staticmethod
    def validate(expert_def: dict) -> tuple[bool, str]:
        """Validate expert definition. Returns (valid, error_message)."""
        missing = REQUIRED_FIELDS - set(expert_def.keys())
        if missing:
            return False, f"Missing fields: {missing}"

        if not isinstance(expert_def["trigger_keywords"], list) or len(expert_def["trigger_keywords"]) == 0:
            return False, "trigger_keywords must be a non-empty list"

        conf = expert_def["trigger_min_confidence"]
        if not isinstance(conf, (int, float)) or not (0.0 < conf <= 1.0):
            return False, f"trigger_min_confidence must be in (0, 1], got {conf}"

        for step in expert_def["pipeline"]:
            if step not in VALID_PIPELINE_STEPS:
                return False, f"Invalid pipeline step: {step}"

        lifecycle = expert_def.get("lifecycle", "new")
        if lifecycle not in VALID_LIFECYCLES:
            return False, f"Invalid lifecycle: {lifecycle}"

        return True, ""
