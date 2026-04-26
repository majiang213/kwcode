"""
AB tester: three-gate expert validation system (spec §5.1).

Gate 1: Quantity check (handled by PatternDetector — >=5 successful same-type tasks)
Gate 2: Backtest against original trajectories
Gate 3: Production AB test (10 tasks: 5 new vs 5 baseline)
"""

import json
import logging
import os
from pathlib import Path

from kaiwu.flywheel.trajectory_collector import TrajectoryCollector, TaskTrajectory
from kaiwu.registry.expert_registry import ExpertRegistry
from kaiwu.registry.expert_loader import ExpertLoader

logger = logging.getLogger(__name__)

CANDIDATES_DIR = os.path.join(Path.home(), ".kaiwu", "candidates")


class ABTester:
    """Three-gate expert validation system."""

    def __init__(self, registry: ExpertRegistry, collector: TrajectoryCollector):
        self.registry = registry
        self.collector = collector
        self._candidates: dict[str, dict] = {}  # expert_name -> candidate info
        self._load_candidates()

    def submit_candidate(self, expert_def: dict, source_trajectories: list[TaskTrajectory]):
        """
        Submit a generated expert for gate 2 (backtest).
        Gate 1 (quantity) was already passed by PatternDetector.

        Gate 2: Backtest validation (simplified for MVP)
        - Validate YAML structure is correct
        - Pipeline matches the source trajectories' pipeline
        - Both conditions met -> enters candidate pool for gate 3
        """
        name = expert_def["name"]

        # Gate 2a: Validate expert definition structure
        valid, err = ExpertLoader.validate(expert_def)
        if not valid:
            logger.warning("Gate 2 failed for %s: validation error: %s", name, err)
            return

        # Gate 2b: Pipeline must match source trajectories
        source_pipeline = source_trajectories[0].pipeline_steps if source_trajectories else []
        if expert_def.get("pipeline") != source_pipeline:
            logger.warning(
                "Gate 2 failed for %s: pipeline mismatch (expert=%s, source=%s)",
                name, expert_def.get("pipeline"), source_pipeline,
            )
            return

        # Gate 2c: Compute baseline stats from source trajectories
        baseline_latency = (
            sum(t.latency_s for t in source_trajectories) / len(source_trajectories)
            if source_trajectories else 0.0
        )

        candidate = {
            "expert_def": expert_def,
            "gate2_passed": True,
            "baseline_success_rate": 1.0,  # All source trajectories were successful
            "baseline_avg_latency": round(baseline_latency, 2),
            "ab_results": [],  # gate 3 results
            "status": "ab_testing",  # ab_testing | graduated | archived
        }
        self._candidates[name] = candidate
        self._save_candidates()

        logger.info("Gate 2 passed for %s. Entering AB test pool.", name)

    def get_candidate_status(self, expert_name: str) -> dict | None:
        """Get current status of a candidate expert."""
        return self._candidates.get(expert_name)

    def should_use_candidate(self, expert_type: str) -> dict | None:
        """
        Check if there's a candidate in AB testing for this expert_type.
        Returns the candidate expert_def if the next task should use it, None otherwise.
        Alternates: odd-numbered tasks use candidate, even use baseline.
        """
        for name, info in self._candidates.items():
            if info["status"] != "ab_testing":
                continue
            if info["expert_def"].get("type") != expert_type:
                continue

            total = len(info["ab_results"])
            if total >= 10:
                continue  # Already has enough data, pending graduation check

            # Alternate: use candidate on odd tasks (0-indexed: 0,2,4 = baseline; 1,3,5 = candidate)
            use_new = total % 2 == 1
            if use_new:
                return info["expert_def"]

        return None

    def record_ab_result(self, expert_name: str, used_new: bool, success: bool, latency: float):
        """
        Record an AB test result for gate 3.

        Gate 3: Production validation (AB test)
        - Next 10 same-type tasks: 5 use new expert, 5 use baseline
        - New expert success_rate > baseline + 10%
        - Pass -> register as lifecycle=new
        - Fail -> archive
        """
        candidate = self._candidates.get(expert_name)
        if not candidate or candidate["status"] != "ab_testing":
            return

        candidate["ab_results"].append({
            "used_new": used_new,
            "success": success,
            "latency": round(latency, 2),
        })
        self._save_candidates()

        logger.debug(
            "AB result for %s: used_new=%s success=%s (%d/10)",
            expert_name, used_new, success, len(candidate["ab_results"]),
        )

    def check_graduation(self, expert_name: str) -> str:
        """
        Check if candidate should graduate or be archived.
        Returns 'pending' | 'graduated' | 'archived'.
        """
        candidate = self._candidates.get(expert_name)
        if not candidate:
            return "pending"

        results = candidate["ab_results"]
        if len(results) < 10:
            return "pending"

        # Split results
        new_results = [r for r in results if r["used_new"]]
        baseline_results = [r for r in results if not r["used_new"]]

        if not new_results or not baseline_results:
            return "pending"

        new_sr = sum(1 for r in new_results if r["success"]) / len(new_results)
        baseline_sr = sum(1 for r in baseline_results if r["success"]) / len(baseline_results)

        # Gate 3: new must beat baseline by >10%
        if new_sr > baseline_sr + 0.10:
            # Graduate: register as lifecycle=new
            expert_def = candidate["expert_def"]
            expert_def["lifecycle"] = "new"
            self.registry.register(expert_def)
            self.registry.save_to_disk(expert_def["name"])
            candidate["status"] = "graduated"
            self._save_candidates()
            logger.info(
                "Expert %s graduated! new_sr=%.0f%% baseline_sr=%.0f%%",
                expert_name, new_sr * 100, baseline_sr * 100,
            )
            return "graduated"

        # Failed gate 3 -> archive
        candidate["status"] = "archived"
        self._save_candidates()
        logger.info(
            "Expert %s archived. new_sr=%.0f%% baseline_sr=%.0f%% (needed +10%%)",
            expert_name, new_sr * 100, baseline_sr * 100,
        )
        return "archived"

    # ── Persistence ──

    def _save_candidates(self):
        """Persist candidate state to disk."""
        os.makedirs(CANDIDATES_DIR, exist_ok=True)
        path = os.path.join(CANDIDATES_DIR, "candidates.json")
        # Serialize: strip non-serializable fields
        data = {}
        for name, info in self._candidates.items():
            data[name] = {
                "expert_def": info["expert_def"],
                "gate2_passed": info["gate2_passed"],
                "baseline_success_rate": info["baseline_success_rate"],
                "baseline_avg_latency": info["baseline_avg_latency"],
                "ab_results": info["ab_results"],
                "status": info["status"],
            }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("Failed to save candidates: %s", e)

    def _load_candidates(self):
        """Load candidate state from disk."""
        path = os.path.join(CANDIDATES_DIR, "candidates.json")
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._candidates = json.load(f)
        except Exception as e:
            logger.warning("Failed to load candidates: %s", e)
            self._candidates = {}
