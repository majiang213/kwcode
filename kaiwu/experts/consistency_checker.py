"""
Consistency Checker: deterministic frontend/backend API alignment check.
Based on rig.json, no LLM calls. Outputs inconsistency list for subtask input.
Theory: RIG (arXiv:2601.10112) + CodeCompass tool adoption findings.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class ConsistencyChecker:
    """
    Deterministic checker that compares backend API routes with frontend API calls
    using rig.json. No LLM involved — pure set operations.
    """

    def __init__(self, project_root: str):
        self.project_root = project_root
        self._rig_path = os.path.join(project_root, ".kaiwu", "rig.json")

    def load_rig(self) -> Optional[dict]:
        """Load rig.json. Returns None if not found."""
        if not os.path.exists(self._rig_path):
            logger.warning("[consistency] rig.json not found at %s", self._rig_path)
            return None
        try:
            with open(self._rig_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[consistency] failed to load rig.json: %s", e)
            return None

    def check(self, rig: Optional[dict] = None) -> dict:
        """
        Run consistency check. Returns structured result:
        {
            "backend_only": ["POST /logout", ...],  # backend has route, frontend doesn't call
            "frontend_only": ["DELETE /user", ...],  # frontend calls, backend has no route
            "matched": ["POST /login", ...],         # both sides agree
            "total_backend": int,
            "total_frontend": int,
            "consistent": bool,
        }
        """
        if rig is None:
            rig = self.load_rig()
        if rig is None:
            return {
                "backend_only": [],
                "frontend_only": [],
                "matched": [],
                "total_backend": 0,
                "total_frontend": 0,
                "consistent": True,
                "error": "rig.json not found",
            }

        backend_routes = set(rig.get("api_routes", {}).keys())
        frontend_calls = set(rig.get("frontend_api_calls", {}).keys())

        # Normalize paths for comparison (strip trailing slashes, lowercase method)
        backend_normalized = {self._normalize_route(r) for r in backend_routes}
        frontend_normalized = {self._normalize_route(r) for r in frontend_calls}

        matched = backend_normalized & frontend_normalized
        backend_only = backend_normalized - frontend_normalized
        frontend_only = frontend_normalized - backend_normalized

        result = {
            "backend_only": sorted(backend_only),
            "frontend_only": sorted(frontend_only),
            "matched": sorted(matched),
            "total_backend": len(backend_routes),
            "total_frontend": len(frontend_calls),
            "consistent": len(backend_only) == 0 and len(frontend_only) == 0,
        }

        if not result["consistent"]:
            logger.info(
                "[consistency] inconsistencies found: %d backend-only, %d frontend-only",
                len(backend_only), len(frontend_only),
            )

        return result

    def check_with_details(self, rig: Optional[dict] = None) -> dict:
        """
        Extended check that includes file location details for each inconsistency.
        Useful as subtask input for Generator.
        """
        if rig is None:
            rig = self.load_rig()
        if rig is None:
            return {"inconsistencies": [], "consistent": True, "error": "rig.json not found"}

        base_result = self.check(rig)
        inconsistencies = []

        api_routes = rig.get("api_routes", {})
        frontend_calls = rig.get("frontend_api_calls", {})

        for route in base_result["backend_only"]:
            # Find the original (non-normalized) key
            original_key = self._find_original_key(route, api_routes)
            location = api_routes.get(original_key, "unknown")
            inconsistencies.append({
                "type": "backend_only",
                "route": route,
                "backend_location": location,
                "frontend_location": None,
                "suggestion": f"前端缺少对 {route} 的调用，后端定义在 {location}",
            })

        for route in base_result["frontend_only"]:
            original_key = self._find_original_key(route, frontend_calls)
            location = frontend_calls.get(original_key, "unknown")
            inconsistencies.append({
                "type": "frontend_only",
                "route": route,
                "backend_location": None,
                "frontend_location": location,
                "suggestion": f"后端缺少 {route} 路由，前端调用在 {location}",
            })

        return {
            "inconsistencies": inconsistencies,
            "consistent": base_result["consistent"],
            "summary": base_result,
        }

    def format_for_subtask(self, rig: Optional[dict] = None) -> str:
        """
        Format inconsistency check result as text suitable for injection
        into a subtask's user_input.
        """
        result = self.check_with_details(rig)
        if result["consistent"]:
            return "前后端接口一致性检查通过，无不一致项。"

        lines = ["## 前后端接口不一致清单\n"]
        for item in result["inconsistencies"]:
            if item["type"] == "backend_only":
                lines.append(f"- [后端独有] {item['route']} → {item['backend_location']}")
            else:
                lines.append(f"- [前端独有] {item['route']} → {item['frontend_location']}")

        summary = result["summary"]
        lines.append(f"\n总计: 后端{summary['total_backend']}个路由, "
                     f"前端{summary['total_frontend']}个调用, "
                     f"匹配{len(summary['matched'])}个")
        return "\n".join(lines)

    @staticmethod
    def _normalize_route(route: str) -> str:
        """Normalize route for comparison: uppercase method, strip trailing slash."""
        parts = route.split(" ", 1)
        if len(parts) == 2:
            method, path = parts
            path = path.rstrip("/")
            if not path:
                path = "/"
            return f"{method.upper()} {path}"
        return route.upper().rstrip("/")

    @staticmethod
    def _find_original_key(normalized: str, mapping: dict) -> str:
        """Find the original key in mapping that matches the normalized form."""
        for key in mapping:
            parts = key.split(" ", 1)
            if len(parts) == 2:
                method, path = parts
                candidate = f"{method.upper()} {path.rstrip('/') or '/'}"
                if candidate == normalized:
                    return key
        return normalized
