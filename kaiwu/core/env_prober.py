"""
EnvProber: 任务开始前确定性探测并修复环境。
不走LLM，所有步骤失败静默。
结果缓存到 .kaiwu/env_profile.json（24小时有效，只缓存成功）。
"""

import glob
import json
import logging
import os
import time
from typing import Optional

__all__ = ["EnvProber", "LANG_TOOLCHAIN"]

logger = logging.getLogger(__name__)

LANG_TOOLCHAIN = {
    "go": {
        "check": "go version",
        "install": "apt-get install -y golang-go",
        "dep_cmd": "go mod download",
        "dep_file": "go.mod",
    },
    "typescript": {
        "check": "npx --version",
        "install": "apt-get install -y nodejs npm",
        "dep_cmd": "npm install",
        "dep_file": "package.json",
    },
    "javascript": {
        "check": "node --version",
        "install": "apt-get install -y nodejs npm",
        "dep_cmd": "npm install",
        "dep_file": "package.json",
    },
    "rust": {
        "check": "cargo --version",
        "install": "curl -sSf https://sh.rustup.rs | sh -s -- -y",
        "dep_cmd": "cargo fetch",
        "dep_file": "Cargo.toml",
    },
    "java": {
        "check": "javac -version",
        "install": "apt-get install -y default-jdk maven",
        "dep_cmd": "mvn dependency:resolve -q",
        "dep_file": "pom.xml",
    },
    "python": {
        "check": "python3 --version",
        "install": "apt-get install -y python3 python3-pip",
        "dep_cmd": "pip install -r requirements.txt",
        "dep_file": "requirements.txt",
    },
}

# 验证命令（确认工具链可用）和实际测试命令分离
_VERIFY_CMDS = {
    "python": ["python -m pytest --version", "python -m pytest -x -q --co -q"],
    "go": ["go build ./..."],
    "typescript": ["npx tsc --version"],
    "javascript": ["node --version"],
    "rust": ["cargo check"],
    "java": ["mvn validate -q"],
}

_TEST_CMDS = {
    "python": "python -m pytest -x -q",
    "go": "go test ./...",
    "typescript": "npx jest --passWithNoTests",
    "javascript": "npx jest --passWithNoTests",
    "rust": "cargo test",
    "java": "mvn test -q",
}


class EnvProber:
    """
    任务开始前自动探测并修复环境。
    确定性逻辑，不走LLM，所有步骤失败静默。
    """

    CACHE_FILE = ".kaiwu/env_profile.json"
    CACHE_TTL_HOURS = 24

    def probe_and_fix(self, project_root: str, tools) -> dict:
        """
        返回：{
            "lang": str,
            "ready": bool,
            "installed": list[str],
            "test_cmd": str,       # 已验证可用的测试命令
            "rig_built": bool,
        }
        """
        # 检查缓存
        cached = self._load_cache(project_root)
        if cached:
            return cached

        lang = self._detect_lang(project_root)
        result = {
            "lang": lang,
            "ready": False,
            "installed": [],
            "test_cmd": "",
            "rig_built": False,
        }

        # 1. 工具链检测和安装
        tc = LANG_TOOLCHAIN.get(lang, {})
        if tc and tc.get("check"):
            try:
                _, _, rc = tools.run_bash(tc["check"], cwd=project_root)
                if rc != 0:
                    _, _, install_rc = tools.run_bash(
                        tc["install"], cwd=project_root, timeout=120
                    )
                    if install_rc == 0:
                        result["installed"].append(f"toolchain:{lang}")
            except Exception as e:
                logger.debug("Toolchain check failed: %s", e)

        # 2. 项目依赖安装
        if tc and tc.get("dep_file"):
            dep_file = os.path.join(project_root, tc["dep_file"])
            if os.path.exists(dep_file):
                try:
                    _, _, rc = tools.run_bash(
                        tc["dep_cmd"], cwd=project_root, timeout=180
                    )
                    if rc == 0:
                        result["installed"].append(f"deps:{tc['dep_file']}")
                except Exception as e:
                    logger.debug("Dep install failed: %s", e)

        # Python额外：pip install -e . / pyproject.toml
        if lang == "python":
            for pkg_file, cmd in [
                ("pyproject.toml", "pip install -e ."),
                ("setup.py", "pip install -e ."),
            ]:
                if os.path.exists(os.path.join(project_root, pkg_file)):
                    try:
                        tools.run_bash(cmd, cwd=project_root, timeout=120)
                    except Exception:
                        pass
                    break  # 只执行一个

        # 3. 预构建rig.json（避免任务中超时）
        rig_path = os.path.join(project_root, ".kaiwu", "rig.json")
        if not os.path.exists(rig_path):
            try:
                from kaiwu.ast_engine.graph_builder import GraphBuilder
                GraphBuilder(project_root).build()
                result["rig_built"] = True
            except Exception:
                pass

        # 4. 验证测试命令可用
        result["test_cmd"] = self._find_working_test_cmd(lang, project_root, tools)
        result["ready"] = bool(result["test_cmd"])

        # 缓存结果（只缓存成功）
        self._save_cache(project_root, result)
        return result

    def _detect_lang(self, project_root: str) -> str:
        """按文件扩展名统计主语言。"""
        counts = {}
        ext_map = [
            ("**/*.go", "go"),
            ("**/*.ts", "typescript"),
            ("**/*.tsx", "typescript"),
            ("**/*.js", "javascript"),
            ("**/*.jsx", "javascript"),
            ("**/*.rs", "rust"),
            ("**/*.java", "java"),
            ("**/*.py", "python"),
        ]
        for pattern, lang in ext_map:
            files = glob.glob(os.path.join(project_root, pattern), recursive=True)
            # 排除node_modules, .git, venv等
            files = [f for f in files if not any(
                skip in f for skip in ('node_modules', '.git', 'venv', '__pycache__', 'target')
            )]
            if files:
                counts[lang] = counts.get(lang, 0) + len(files)

        return max(counts, key=counts.get) if counts else "python"

    def _find_working_test_cmd(self, lang: str, project_root: str, tools) -> str:
        """验证工具链可用后返回测试命令。go用build验证（spec v2修正）。"""
        verify_cmds = _VERIFY_CMDS.get(lang, [])

        for verify_cmd in verify_cmds:
            try:
                _, _, rc = tools.run_bash(verify_cmd, cwd=project_root, timeout=30)
                if rc == 0:
                    return _TEST_CMDS.get(lang, "")
            except Exception:
                continue

        return ""

    def _load_cache(self, project_root: str) -> Optional[dict]:
        """加载缓存，过期返回None。"""
        cache_path = os.path.join(project_root, self.CACHE_FILE)
        try:
            if not os.path.exists(cache_path):
                return None
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            # TTL检查
            cached_at = cache.get("cached_at", 0)
            if time.time() - cached_at > self.CACHE_TTL_HOURS * 3600:
                return None
            return cache
        except Exception:
            return None

    def _save_cache(self, project_root: str, result: dict):
        """只缓存成功的结果。"""
        if not result.get("ready"):
            return  # 失败不缓存，下次任务重试

        cache_path = os.path.join(project_root, self.CACHE_FILE)
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            cache = {**result, "cached_at": time.time()}
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception as e:
            logger.debug("Cache save failed: %s", e)
