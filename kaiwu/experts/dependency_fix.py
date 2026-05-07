"""
DependencyFixExpert: 确定性依赖安装。
从ImportError/ModuleNotFoundError提取模块名，pip/npm/go get安装。
不走LLM。
"""

import logging
import re
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.core.gap_detector import GapType
from kaiwu.tools.executor import ToolExecutor

__all__ = ["DependencyFixExpert"]

logger = logging.getLogger(__name__)

# 常见的包名映射（PyPI名 ≠ import名的情况）
_PYPI_NAME_MAP = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "gi": "PyGObject",
    "attr": "attrs",
    "serial": "pyserial",
    "usb": "pyusb",
    "wx": "wxPython",
    "Crypto": "pycryptodome",
}


class DependencyFixExpert:
    """
    处理依赖缺失。
    确定性逻辑：从ImportError提取模块名，pip install安装。
    不走LLM。
    """

    def __init__(self, tool_executor: ToolExecutor):
        self.tools = tool_executor

    def can_handle(self, ctx: TaskContext) -> tuple[bool, float]:
        """确定性判断：gap为MISSING_DEP时处理。"""
        gap = ctx.gap
        if gap and hasattr(gap, 'gap_type'):
            if gap.gap_type == GapType.MISSING_DEP:
                return True, 0.95
        return False, 0.0

    def run(self, ctx: TaskContext) -> Optional[dict]:
        """提取缺失包名并安装。返回env_changed标记。"""
        error_output = getattr(ctx, 'initial_test_failure', '') or ''
        if not error_output and ctx.gap:
            error_output = ctx.gap.error_msg

        missing = self._extract_missing_packages(error_output)
        if not missing:
            return None

        installed_any = False
        installed_pkgs = []

        for pkg in missing:
            # 查找PyPI名映射
            pypi_name = _PYPI_NAME_MAP.get(pkg, pkg)
            install_cmd = f"pip install {pypi_name}"

            try:
                _, stderr, rc = self.tools.run_bash(
                    install_cmd, cwd=ctx.project_root, timeout=60
                )
                if rc == 0:
                    installed_any = True
                    installed_pkgs.append(pypi_name)
                    logger.info("[dep_fix] installed %s", pypi_name)
                else:
                    logger.debug("[dep_fix] failed to install %s: %s", pypi_name, stderr[:100])
            except Exception as e:
                logger.debug("[dep_fix] install exception: %s", e)

        if installed_any:
            return {
                "patches": [],
                "env_changed": True,
                "explanation": f"已安装依赖：{', '.join(installed_pkgs)}",
            }

        return None  # 安装失败，让orchestrator走别的路

    def _extract_missing_packages(self, error_output: str) -> list[str]:
        """只提取 No module named 'xxx'，不提取 from 'yyy'（会误匹配模块路径）。"""
        # 只匹配这一种格式，最准确
        pkgs = re.findall(r"No module named '([^']+)'", error_output)
        # 去掉子模块，只取顶层包名: "foo.bar" → "foo"
        return list(set(pkg.split(".")[0] for pkg in pkgs))
