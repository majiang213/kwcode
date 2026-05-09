"""
Generator expert: generates code patches based on Locator output.
RED-2: Deterministic pipeline, generates multiple candidates at fixed temperatures.
RED-3: Independent context window, only sees Locator output + relevant snippets.

Key design: original is read directly from file (never LLM-generated),
LLM only produces the modified version. This guarantees apply_patch exact match.
"""

import json
import logging
import re
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

GENERATOR_BASE_SYSTEM = """## 行为准则（硬约束）
修改范围:
- 每次patch只修改≤2个函数，修改行数≤30行。
- 不触碰报错行±20行范围外的无关代码。
- 不添加任何import/类型注解/docstring/注释到未修改的代码。
- 例外：任务明确要求重构/拆分时，不受上述行数限制。
- 例外：存根实现任务（scope=whole_file）时，实现所有目标函数，不受2个函数限制。

禁止操作:
- 禁止猜测API端点/函数签名/配置键——必须先read_file确认。
- 禁止编造不存在的包名或CLI参数。
- 禁止write_file后立即read_file验证——信任工具返回值。
- 禁止测试通过后"再确认一下"。

输出格式:
- 数据文件(csv/json/yaml/toml)必须用ASCII标点：冒号:不用：逗号,不用，
- 代码文件禁止中文标点。
- 输出被截断时拆成≤200行的片段。
"""

WEB_DESIGN_RULES = """\
## 网页设计规范（生成HTML/CSS时必须遵守）

### 设计思考（写代码前必做）
锁定一个大胆的视觉方向并贯彻到底。不要折中妥协。
交付的代码必须：生产级可用、视觉震撼、风格统一。

### 字体
- 用Google Fonts，选有个性的字体，不要通用字体
- 禁止：Arial、Roboto、system-ui、sans-serif作为主字体
- 标题用展示字体，正文用精致的阅读字体
- 推荐组合：Playfair Display+Lato（奢华）、Space Mono+Inter（科技）、Cormorant Garamond+Source Sans Pro（杂志）

### 配色
- 锁定一套有主见的配色，用CSS变量保持一致
- 主色压倒性占比，1-2个配色，1个强调色
- 禁止：白底紫色渐变、千篇一律的蓝白配色
- 深色背景往往比浅色更有视觉冲击力

### 布局
- 打破预期：不对称、叠加、对角线流向
- 不要每个卡片都一样大，不要每行都一样高
- 慷慨的留白 OR 精心控制的密度——二选一

### 背景与氛围
不要默认纯色背景，用渐变网格、噪点纹理、几何图案、透明度叠加、多层阴影营造深度。

### 动效
- 优先纯CSS动画，不引入额外JS库
- 页面加载入场动效（animation-delay错开）
- 所有交互元素加 hover 状态 + transition 200-300ms

### 技术规范
- 用Tailwind CDN：<script src="https://cdn.tailwindcss.com"></script>
- Google Fonts CDN引入
- 响应式：grid-cols-1 md:grid-cols-2 lg:grid-cols-3
- 毛玻璃：backdrop-blur-md bg-white/10 border border-white/20
- 渐变文字：bg-gradient-to-r bg-clip-text text-transparent
- 导航栏：fixed top-0 z-50 backdrop-blur-md

### 禁止
- 白底黑字无样式的默认页面
- 每个卡片一模一样的布局
- 缺少视觉重心（所有元素同等权重）
- 不同任务生成同样的审美风格

### 自检清单
- 字体有个性，不是Arial/Roboto
- 配色有主见，主色压倒性占比
- 背景有氛围（渐变/纹理），不是平铺纯色
- 有入场动效（至少fadeIn）
- 所有交互元素有hover+transition
- 移动端响应式，不溢出
"""

# 网页任务检测关键词
_WEB_KEYWORDS = {"html", "css", "web", "网页", "页面", "前端", "界面", "landing",
                 "website", "网站", "落地页", "登录页", "注册页", "dashboard", "tailwind"}

GENERATOR_PROMPT = """你是代码修复专家。

任务：{task_description}

原始代码（来自 {file_path}）：
{original_code}

{search_context}

输出修改后的完整函数。格式要求：
- 保持原始缩进（class内方法必须4空格，与原文件完全一致）
- 只输出函数代码，从def/class开始
- 不超过15行修改
- 纯代码，无markdown，无解释"""

HASHLINE_PROMPT = """你是代码修复专家。用锚点编辑指令修改代码，不要复现整个函数。

任务描述：{task_description}

代码（来自 {file_path}，每行格式: 行号|哈希|内容）：
{anchored_code}

{search_context}

只输出编辑指令，每行一条，格式：
  EDIT 行号|哈希| → 新内容
  DELETE 行号|哈希|
  INSERT_AFTER 行号|哈希| → 新行内容

规则：
1. 只修改需要改的行，≤20条指令
2. 哈希必须与上面代码中的完全一致
3. 保持原始缩进风格
4. 可以用多条INSERT_AFTER在同一位置插入多行（按顺序排列）
5. 不要输出其他内容，只输出编辑指令"""

GENERATOR_NEWFILE_PROMPT = """你是代码生成专家。

任务：{task_description}
目标文件：{target_file}

相关上下文：
{code_snippets}

{search_context}

直接输出文件完整内容：
- 纯代码，无markdown，无解释
- 涉及实时数据时使用占位符[数据加载中]，不编造数据"""

GENERATOR_TEST_PROMPT = """你是测试生成专家。为下面的代码生成 pytest 单元测试。

源代码（来自 {source_file}）：
{source_code}

任务：{task_description}

{search_context}

输出完整的pytest测试文件：
- 文件开头import被测模块
- 每个函数至少2个测试用例（正常+边界）
- 使用assert语句
- 纯代码，无markdown，无解释"""


_LANG_KEYWORDS = {
    ".html": ["html", "网页", "页面", "web page", "webpage", "website", "前端页面"],
    ".js":   ["javascript", "js", "node", "nodejs", "react", "vue"],
    ".ts":   ["typescript", "ts", "angular"],
    ".css":  ["css", "样式", "stylesheet"],
    ".java": ["java", "spring", "springboot"],
    ".go":   ["golang", "go语言"],
    ".rs":   ["rust"],
    ".c":    ["c语言", "c程序"],
    ".cpp":  ["c++", "cpp"],
    ".sh":   ["shell", "bash", "脚本"],
    ".sql":  ["sql", "数据库查询"],
    ".json": ["json"],
    ".yaml": ["yaml", "yml"],
}


def _detect_extension(user_input: str) -> str:
    """从用户输入推断目标文件扩展名。默认.py。"""
    lower = user_input.lower()
    for ext, keywords in _LANG_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return ext
    return ".py"


class GeneratorExpert:
    """Generates code patches. Original is read from file, LLM only generates modified."""

    def __init__(self, llm: LLMBackend, tool_executor: ToolExecutor = None, num_candidates: int = 3):
        self.llm = llm
        self.tools = tool_executor
        self.num_candidates = num_candidates
        self.temperatures = [0.0, 0.3, 0.6][:num_candidates]

    def run(self, ctx: TaskContext) -> Optional[dict]:
        """
        Generate patches. For each target function:
        1. Read original code directly from file (100% accurate)
        2. Ask LLM to generate only the modified version
        3. Package as {file, original, modified} patch
        """
        locator = ctx.locator_output or {}
        files = locator.get("relevant_files", [])
        funcs = locator.get("relevant_functions", [])

        if not files:
            # No locator output — pure codegen task
            return self._run_codegen(ctx)

        # Detect test generation tasks — need to CREATE test file, not modify source
        if self._is_test_generation_task(ctx):
            return self._run_test_generation(ctx, files)

        # ── whole_file scope: 存根实现 OR 需要整文件改动的任务 ──
        # Sub-task decomposition: 逐函数独立实现，每个函数独立context
        if ctx.gap and hasattr(ctx.gap, 'gap_type'):
            from kaiwu.core.gap_detector import GapType
            if ctx.gap.gap_type in (GapType.NOT_IMPLEMENTED, GapType.STUB_RETURNS_NONE):
                return self._run_stub_decomposed(ctx, files, funcs)

        # 需要整文件scope的任务：重构（提取函数/拆分类）或综合任务（bug+refactor）
        # 判断条件：任务需要新增类/函数，或测试要求代码行数缩短/拆分
        if self._needs_whole_file_scope(ctx, files, funcs):
            # Retry时用targeted_fix（基于当前磁盘状态，带函数级定位）
            if ctx.best_tests_passed > 0:
                return self._run_targeted_fix(ctx, files)
            # 首次attempt始终用whole_file_refactor（更稳定，t04靠这个PASS）
            return self._run_whole_file_refactor(ctx, files)

        # For each file+function pair, extract original and generate modified
        # Deduplicate: only patch each (file, function) once
        patches = []
        explanation_parts = []
        seen = set()  # (file, func) pairs already processed

        for fpath in files[:3]:  # Cap at 3 files
            # Skip test files — we only modify source code
            if "test" in fpath.lower():
                continue

            # Read the actual file content
            if self.tools:
                content = self.tools.read_file(fpath)
            else:
                content = ctx.relevant_code_snippets.get(fpath, "")
            if not content or content.startswith("[ERROR]"):
                continue

            # Find target functions in this file (deduplicated)
            file_funcs = list(dict.fromkeys(
                f for f in funcs if self._func_in_file(f, content)
            ))
            if not file_funcs:
                snippet = ctx.relevant_code_snippets.get(fpath, "")
                if snippet:
                    file_funcs = ["_whole_snippet_"]

            # MoE: scope决定每个文件处理多少函数
            # whole_file scope时不限制2个，处理所有target_functions
            _max_funcs_per_file = 2  # 默认cap
            if ctx.gap and hasattr(ctx.gap, 'gap_type'):
                from kaiwu.core.gap_detector import GapType
                if ctx.gap.gap_type in (GapType.NOT_IMPLEMENTED, GapType.STUB_RETURNS_NONE):
                    _max_funcs_per_file = len(file_funcs)  # 不限制

            for func_name in file_funcs[:_max_funcs_per_file]:
                key = (fpath, func_name)
                if key in seen:
                    continue
                seen.add(key)
                if func_name == "_whole_snippet_":
                    original = ctx.relevant_code_snippets.get(fpath, content[:2000])
                else:
                    # Extract the exact function text from file
                    original = self._extract_function(content, func_name)
                    if not original:
                        logger.warning("Could not extract function %s from %s", func_name, fpath)
                        continue

                # Ask LLM to generate only the modified version
                modified = self._generate_modified(
                    ctx, fpath, original, ctx.user_input
                )
                if not modified:
                    continue

                # 对齐缩进：LLM返回的modified可能丢失class内方法的缩进
                modified = modified.strip("\n")  # 清掉前后空行再对齐
                modified = self._align_indentation(original, modified)

                # Verify original exists in file (should always be true since we read it)
                if original not in content:
                    logger.error("Extracted original not found in file — this should not happen")
                    continue

                patches.append({
                    "file": fpath,
                    "original": original,
                    "modified": modified,
                })
                explanation_parts.append(f"{fpath}:{func_name}")

        if not patches:
            # Fallback: 逐函数patch全失败时，尝试whole_file路径
            logger.warning("Generator: no patches produced, trying whole_file fallback")
            return self._run_whole_file(ctx, files)

        result = {
            "patches": patches,
            "explanation": f"Modified: {', '.join(explanation_parts)}",
        }
        ctx.generator_output = result
        return result

    def _build_system(self, ctx: TaskContext, base_system: str = "") -> str:
        """Combine expert_system_prompt (from registry) with base system prompt.
        Appends tier-specific constraints and WEB_DESIGN_RULES when applicable."""
        expert_prompt = ctx.expert_system_prompt or ""
        base = base_system or GENERATOR_BASE_SYSTEM
        if expert_prompt:
            system = f"{expert_prompt}\n\n{base}"
        else:
            system = base

        # 模型能力自适应：按tier注入不同强度的格式约束
        tier = getattr(ctx, 'model_tier', '')
        if tier == "small":
            # 小模型：不告诉工具存在（避免混乱输出工具调用文本），严格格式约束
            system += (
                "\n\n## 格式约束（严格执行）\n"
                "- 只输出代码，禁止任何解释、注释、命令\n"
                "- 每次只修改1个函数，修改行数≤10行\n"
                "- class内方法必须保持原有缩进（通常4空格）\n"
                "- 禁止输出markdown代码块标记（```）\n"
                "- 禁止输出write_file、read_file等命令文本\n"
                "\n## 编写规范（严格遵守）\n"
                "生成代码时使用以下框架，只填写TODO部分：\n"
                "- 函数签名保持原样不变\n"
                "- 只替换函数体，不改其他\n"
                "- 每个TODO只写3-5行，超过就分解\n"
            )
        elif tier == "large":
            # 大模型：保留工具描述但明确自动调用
            system += (
                "\n\n## 工具与格式\n"
                "- 工具（read_file/write_file/run_bash）由系统自动调用，你只需输出修改后的代码\n"
                "- 不要输出工具调用命令\n"
                "- 保持代码风格一致，缩进与原文件匹配\n"
            )
        else:
            # medium: 简洁工具说明
            system += (
                "\n\n## 工具说明\n"
                "- 工具由系统自动调用，你只需输出修改后的代码，不要输出工具调用命令\n"
            )

        # Inject upstream constraints into system prompt
        upstream = getattr(ctx, 'upstream_constraints', '')
        if upstream:
            system += f"\n\n## 跨文件约束（必须遵守）\n{upstream}"

        # Append web design rules for HTML/CSS/web tasks
        if self._is_web_task(ctx.user_input):
            system = f"{system}\n\n{WEB_DESIGN_RULES}"
        return system

    @staticmethod
    def _is_web_task(user_input: str) -> bool:
        """Detect if the task involves web/HTML/CSS generation."""
        lower = user_input.lower()
        return any(kw in lower for kw in _WEB_KEYWORDS)

    def _generate_modified(self, ctx: TaskContext, fpath: str, original: str, task_desc: str) -> Optional[str]:
        """Ask LLM to generate modified code. Hashline primary, full-function fallback.
        Bounded context: only pass current function + relevant failing tests."""
        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料：\n{ctx.search_results}"

        # ── Hashline path: anchor-based editing (ALL attempts) ──
        # Retry也优先用hashline，避免full-function生成导致scope爆炸
        result = self._try_hashline(ctx, fpath, original, task_desc, search_ctx)
        if result:
            return result
        # Hashline失败，静默fallback到full-function
        logger.debug("Hashline failed, falling back to full-function generation")

        # ── Fallback: full function generation ──
        # Build prompt based on retry_strategy
        prompt = self._build_retry_prompt(ctx, fpath, original, task_desc, search_ctx)

        # Inject upstream constraints from SearchSubagent (cross-file contracts)
        upstream_constraints = ctx.upstream_constraints
        if upstream_constraints:
            prompt += f"\n\n## 跨文件契约（必须遵守）\n{upstream_constraints}"

        # Append doc_context if available (keep concise)
        if ctx.doc_context:
            prompt += f"\n\n## 相关文档参考\n{ctx.doc_context[:800]}"

        # ── Bounded context: 只注入与当前函数相关的 failing tests ──
        # 从 structured_failures 中筛选与当前函数/文件相关的条目
        initial_failure = getattr(ctx, 'initial_test_failure', '')
        structured = (ctx.verifier_output or {}).get("structured_failures", [])
        if not structured and initial_failure:
            from kaiwu.core.test_parser import parse_test_failures
            structured = parse_test_failures(initial_failure)

        if structured:
            # 提取当前函数名（从 original 的第一行 def/class 获取）
            current_func = self._extract_func_name_from_code(original)
            # 筛选与当前函数/文件相关的失败
            relevant_failures = self._filter_relevant_failures(structured, current_func, fpath)
            if relevant_failures:
                lines = ["## 必须修复的测试失败（精确信息）"]
                for f in relevant_failures[:5]:
                    name = f.get("test_name", "?")
                    expected = f.get("expected", "")
                    actual = f.get("actual", "")
                    snippet = f.get("snippet", "")
                    err_type = f.get("error_type", "")
                    if expected and actual:
                        lines.append(f"- {name}: 期望 {expected}，实际 {actual}")
                    elif err_type and snippet:
                        lines.append(f"- {name}: {err_type}: {snippet[:120]}")
                    elif snippet:
                        lines.append(f"- {name}: {snippet[:120]}")
                    else:
                        lines.append(f"- {name}")
                prompt += "\n\n" + "\n".join(lines)
            elif not relevant_failures and structured:
                # 没有精确匹配时，给前3条作为上下文（但不是全部）
                lines = ["## 相关测试失败"]
                for f in structured[:3]:
                    name = f.get("test_name", "?")
                    snippet = f.get("snippet", "")
                    lines.append(f"- {name}: {snippet[:100]}" if snippet else f"- {name}")
                prompt += "\n\n" + "\n".join(lines)

        # 透传pre_test的失败原始输出（首次和retry都加）
        test_failure = getattr(ctx, 'initial_test_failure', '') or ''
        if test_failure:
            prompt += f"\n\n## 当前测试失败详情\n{test_failure[-2000:]}\n"

        # Inject retry_hint: 只传一句话总结，不传完整历史
        if ctx.retry_hint:
            # 截断 retry_hint，避免注入过多历史
            hint = ctx.retry_hint
            if len(hint) > 300:
                hint = hint[:300] + "..."
            prompt += f"\n\n## 重试提示\n{hint}"

        system = self._build_system(ctx)

        # AdaptThink: 根据think_config调整max_tokens
        base_tokens = 2048
        # scope=whole_file时提升token预算（需要实现多个函数）
        if ctx.gap and hasattr(ctx.gap, 'gap_type'):
            from kaiwu.core.gap_detector import GapType
            if ctx.gap.gap_type in (GapType.NOT_IMPLEMENTED, GapType.STUB_RETURNS_NONE):
                base_tokens = 4096
        think_cfg = getattr(ctx, 'think_config', {})
        if think_cfg.get("think") and self.llm._is_reasoning:
            base_tokens += think_cfg.get("budget", 0)

        # ── Best-of-N采样：生成多个候选，选语法正确的最佳版本 ──
        candidates = []
        for temp in self.temperatures:
            raw = self.llm.generate(prompt=prompt, system=system, max_tokens=base_tokens, temperature=temp)
            self._log_llm_call(ctx, "generator", prompt, system, raw)
            modified = self._clean_code_output(raw)
            if modified and modified != original:
                # Scope guard: 拒绝scope爆炸的输出（LLM输出整个文件而非函数）
                if not self._scope_check(original, modified):
                    logger.warning("Generator: output scope explosion (orig %d lines, mod %d lines), rejected",
                                   original.count('\n'), modified.count('\n'))
                    continue
                if self._is_valid_syntax(modified):
                    # 语法正确，直接返回（优先低温度的确定性结果）
                    return modified
                else:
                    # 语法错误但有内容，存为候选
                    candidates.append(modified)

        # 所有温度都语法错误时，返回第一个候选（让verifier报具体错误）
        if candidates:
            logger.debug("Generator: no syntax-valid candidate, returning best-effort")
            return candidates[0]

        logger.warning("Generator: all candidates identical to original or empty")
        return None

    @staticmethod
    def _is_valid_syntax(code: str) -> bool:
        """检查Python代码语法是否正确。非Python代码直接返回True。"""
        import ast
        stripped = code.strip()
        if not stripped:
            return False
        first_line = stripped.split("\n")[0].strip()
        python_indicators = ("def ", "class ", "import ", "from ", "if ", "for ",
                             "while ", "try:", "with ", "async ", "@")
        if not any(first_line.startswith(p) for p in python_indicators):
            return True  # 非Python代码，跳过语法检查
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    @staticmethod
    def _scope_check(original: str, modified: str) -> bool:
        """Reject outputs where LLM generated an entire file instead of the target function.
        Heuristic: if original starts with 'def'/'class method' but modified contains
        multiple top-level class definitions or is >3x the original size, reject it."""
        orig_lines = [l for l in original.split("\n") if l.strip()]
        mod_lines = [l for l in modified.split("\n") if l.strip()]
        orig_len = max(len(orig_lines), 1)
        mod_len = len(mod_lines)

        # If original is a single function/method, modified shouldn't be an entire class
        orig_first = original.strip().split("\n")[0].strip() if original.strip() else ""
        mod_first = modified.strip().split("\n")[0].strip() if modified.strip() else ""

        # Case 1: original is a function but modified starts with class (scope explosion)
        if orig_first.startswith("def ") and mod_first.startswith("class "):
            return False

        # Case 2: original is a method (indented def) but modified is a top-level class
        if "def " in orig_first and mod_first.startswith("class ") and not orig_first.startswith("class "):
            return False

        # Case 3: modified is way too large compared to original (>3x for functions)
        # Allow more room for class-level originals (refactoring may legitimately grow)
        if orig_first.startswith("class "):
            max_ratio = 5.0  # Classes can grow more during refactoring
        else:
            max_ratio = 3.0  # Functions shouldn't triple in size
        if mod_len > orig_len * max_ratio and mod_len > 50:
            return False

        # Case 4: modified contains multiple top-level class/import statements
        # (sign of entire file output)
        top_level_classes = sum(1 for l in modified.split("\n")
                                if l.strip().startswith("class ") and not l.startswith(" "))
        top_level_imports = sum(1 for l in modified.split("\n")
                                if (l.strip().startswith("import ") or l.strip().startswith("from "))
                                and not l.startswith(" "))
        if top_level_classes >= 2 or (top_level_imports >= 3 and orig_first.startswith("def ")):
            return False

        return True

    @staticmethod
    def _extract_func_name_from_code(code: str) -> str:
        """从代码片段中提取函数/类名。"""
        for line in code.split("\n"):
            stripped = line.strip()
            if stripped.startswith("def "):
                # def func_name(...)
                name = stripped[4:].split("(")[0].strip()
                return name
            if stripped.startswith("class "):
                name = stripped[6:].split("(")[0].split(":")[0].strip()
                return name
        return ""

    @staticmethod
    def _filter_relevant_failures(failures: list, func_name: str, file_path: str) -> list:
        """筛选与当前函数/文件相关的测试失败。"""
        if not func_name:
            return failures[:5]  # 无法确定函数名时返回前5条

        relevant = []
        fname_lower = func_name.lower()
        fpath_base = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if file_path else ""

        for f in failures:
            test_name = f.get("test_name", "").lower()
            snippet = f.get("snippet", "").lower()
            # 测试名包含函数名，或 snippet 中提到函数名/文件名
            if (fname_lower in test_name or
                fname_lower in snippet or
                (fpath_base and fpath_base.lower() in snippet)):
                relevant.append(f)

        return relevant

    def _try_hashline(self, ctx: TaskContext, fpath: str, original: str,
                      task_desc: str, search_ctx: str) -> Optional[str]:
        """Try Hashline anchor-based editing. Returns modified code or None."""
        try:
            from kaiwu.tools.hashline import add_anchors, parse_anchor_edits, apply_anchor_edits
        except ImportError:
            return None

        anchored = add_anchors(original)
        prompt = HASHLINE_PROMPT.format(
            task_description=task_desc,
            file_path=fpath,
            anchored_code=anchored,
            search_context=search_ctx,
        )
        while "\n\n\n" in prompt:
            prompt = prompt.replace("\n\n\n", "\n\n")

        # 注入初始测试失败信息
        initial_failure = getattr(ctx, 'initial_test_failure', '')
        if initial_failure:
            prompt += f"\n\n## 当前测试失败详情\n{initial_failure[-2000:]}\n"

        if ctx.retry_hint:
            prompt += f"\n\n## 重试提示\n{ctx.retry_hint}"

        system = self._build_system(ctx)
        # 根据代码长度和任务复杂度调整token预算
        orig_lines = original.count('\n') + 1
        hashline_tokens = min(2048, max(1024, orig_lines * 30))
        raw = self.llm.generate(prompt=prompt, system=system, max_tokens=hashline_tokens, temperature=0.0)
        self._log_llm_call(ctx, "generator_hashline", prompt, system, raw)
        if not raw or not raw.strip():
            return None

        edits = parse_anchor_edits(raw)
        if not edits:
            logger.debug("Hashline: no valid edit instructions parsed from: %s", raw[:200])
            return None

        modified, errors = apply_anchor_edits(original, edits)
        if errors:
            logger.debug("Hashline: edit rejected — %s", "; ".join(errors))
            return None

        if modified == original:
            logger.debug("Hashline: edits produced no change")
            return None

        logger.info("Hashline: applied %d edits successfully", len(edits))
        return modified

    def _build_retry_prompt(self, ctx: TaskContext, fpath: str, original: str,
                            task_desc: str, search_ctx: str) -> str:
        """Build prompt based on retry_strategy: 0=normal, 1=error-first, 2=minimal."""
        strategy = ctx.retry_strategy
        search_line = f"{search_ctx}\n" if search_ctx else ""

        if strategy == 0:
            prompt = GENERATOR_PROMPT.format(
                task_description=task_desc,
                file_path=fpath,
                original_code=original,
                search_context=search_ctx,
            )
            # Collapse triple+ newlines when search_context is empty
            while "\n\n\n" in prompt:
                prompt = prompt.replace("\n\n\n", "\n\n")
            return prompt

        elif strategy == 1:
            error = ctx.previous_failure or "验证失败"
            reflection_line = f"\n失败分析：{ctx.reflection}" if ctx.reflection else ""
            debug_line = f"\n运行时调试信息：{ctx.debug_info}" if ctx.debug_info else ""
            return (
                f"上次修改失败了。错误信息：\n{error[:500]}{reflection_line}{debug_line}\n\n"
                f"原始代码（来自 {fpath}）：\n```\n{original}\n```\n\n"
                f"{search_line}"
                f"直接修复这个错误。只输出修改后的完整函数代码（从def或class开始到函数结束），不要输出整个文件，不要解释。"
            )

        else:
            error = ctx.previous_failure or "验证失败"
            reflection_line = f"\n上次失败原因：{ctx.reflection}" if ctx.reflection else ""
            debug_line = f"\n运行时调试信息：{ctx.debug_info}" if ctx.debug_info else ""
            return (
                f"只修改以下代码的最小必要部分，其他代码一行都不要动。{reflection_line}{debug_line}\n\n"
                f"需要修复的错误：{error[:300]}\n\n"
                f"原始代码（来自 {fpath}）：\n```\n{original}\n```\n\n"
                f"{search_line}"
                f"输出修改后的完整函数代码（从def或class开始到函数结束）。只改必须改的行，其余保持原样。禁止输出整个文件。"
            )

    def _run_codegen(self, ctx: TaskContext) -> Optional[dict]:
        """Pure code generation (no existing file to patch). Writes to real project path."""
        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料（以下为真实搜索数据，必须使用）：\n{ctx.search_results}"
        elif self._needs_realtime_warning(ctx.user_input):
            search_ctx = "注意：未获取到实时数据。涉及天气、股价、新闻等实时信息时，请使用占位符（如[数据加载中]），不要编造虚假数据。"

        snippets_text = ""
        for fpath, snippet in ctx.relevant_code_snippets.items():
            snippets_text += f"\n--- {fpath} ---\n{snippet}\n"

        # Extract target filename BEFORE prompt so we can tell the model
        target_file = self._extract_filename(ctx.user_input)

        prompt = GENERATOR_NEWFILE_PROMPT.format(
            task_description=ctx.user_input,
            target_file=target_file,
            code_snippets=snippets_text[:3000] if snippets_text else "(无上下文)",
            search_context=search_ctx,
        )

        system = self._build_system(ctx)
        raw = self.llm.generate(prompt=prompt, system=system, max_tokens=2048, temperature=0.0)
        self._log_llm_call(ctx, "generator_codegen", prompt, system, raw)
        code = self._clean_code_output(raw)
        if not code:
            return None

        import os
        full_path = os.path.join(ctx.project_root, target_file)

        # 如果文件已存在，走whole_file覆盖（不生成_1.py）
        if os.path.exists(full_path):
            result = {
                "patches": [{"file": target_file, "content": code, "write_mode": "whole_file"}],
                "explanation": f"已覆盖：{target_file}",
            }
            ctx.generator_output = result
            return result

        result = {
            "patches": [{"file": target_file, "original": "", "modified": code}],
            "explanation": f"已生成：{full_path}",
        }
        ctx.generator_output = result
        return result

    def _is_test_generation_task(self, ctx: TaskContext) -> bool:
        """Detect if the task is about generating tests (not modifying source)."""
        keywords = ["生成测试", "写测试", "单元测试", "test", "pytest", "测试用例", "添加测试"]
        task_lower = ctx.user_input.lower()
        gate_type = ctx.gate_result.get("expert_type", "")
        expert_name = ctx.gate_result.get("expert_name", "")
        if expert_name == "TestGenExpert":
            return True
        if gate_type == "codegen" and any(kw in task_lower for kw in keywords):
            return True
        return any(kw in task_lower for kw in keywords[:3])  # Strong Chinese signals

    def _run_test_generation(self, ctx: TaskContext, source_files: list[str]) -> Optional[dict]:
        """Generate a new test file for the given source files."""
        import os

        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料：\n{ctx.search_results}"

        # Read source files to provide as context
        source_code_parts = []
        primary_source = None
        for fpath in source_files[:3]:
            if "test" in fpath.lower():
                continue
            content = self.tools.read_file(fpath) if self.tools else ""
            if content and not content.startswith("[ERROR]"):
                source_code_parts.append(f"# {fpath}\n{content}")
                if primary_source is None:
                    primary_source = fpath

        if not source_code_parts:
            return self._run_codegen(ctx)

        source_code = "\n\n".join(source_code_parts)

        prompt = GENERATOR_TEST_PROMPT.format(
            source_file=primary_source or "source",
            source_code=source_code[:4000],
            task_description=ctx.user_input,
            search_context=search_ctx,
        )

        system = self._build_system(ctx)
        raw = self.llm.generate(prompt=prompt, system=system, max_tokens=2048, temperature=0.0)
        self._log_llm_call(ctx, "generator_codegen", prompt, system, raw)
        code = self._clean_code_output(raw)
        if not code:
            return None

        # Determine test file path
        test_dir = os.path.join(ctx.project_root, "tests")
        if primary_source:
            base = os.path.splitext(os.path.basename(primary_source))[0]
            test_file = os.path.join("tests", f"test_{base}.py")
        else:
            test_file = os.path.join("tests", "test_generated.py")

        # Ensure tests/ dir and __init__.py exist
        if self.tools:
            abs_test_dir = os.path.join(ctx.project_root, "tests")
            os.makedirs(abs_test_dir, exist_ok=True)
            init_path = os.path.join(abs_test_dir, "__init__.py")
            if not os.path.exists(init_path):
                with open(init_path, "w", encoding="utf-8") as f:
                    pass

        result = {
            "patches": [{"file": test_file, "original": "", "modified": code}],
            "explanation": f"Generated test file for {primary_source or 'source'}",
        }
        ctx.generator_output = result
        return result

    @staticmethod
    def _align_indentation(original: str, modified: str) -> str:
        """
        让modified的基础缩进和original保持一致。
        解决LLM生成class方法时丢失缩进的系统性bug：
        original有4空格缩进（class内方法），但LLM返回0空格（顶层函数），
        apply_patch替换后方法"跑出"class，导致IndentationError。
        """
        orig_lines = original.split("\n")
        mod_lines = modified.split("\n")
        if not orig_lines or not mod_lines:
            return modified

        # 获取original第一个非空行的缩进
        orig_indent = 0
        for line in orig_lines:
            if line.strip():
                orig_indent = len(line) - len(line.lstrip())
                break

        # 获取modified第一个非空行的缩进
        mod_indent = 0
        for line in mod_lines:
            if line.strip():
                mod_indent = len(line) - len(line.lstrip())
                break

        diff = orig_indent - mod_indent
        if diff <= 0:
            return modified  # modified缩进已经>=original，不需要调整

        pad = " " * diff
        aligned = []
        for line in mod_lines:
            if line.strip():  # 非空行加缩进
                aligned.append(pad + line)
            else:
                aligned.append(line)
        return "\n".join(aligned)

    @staticmethod
    def _extract_function(content: str, func_name: str) -> Optional[str]:
        """Extract a complete function/method from file content by name."""
        lines = content.split("\n")
        start_idx = -1
        indent_level = -1

        # Handle "Class.method" names from AST — strip class prefix
        short_name = func_name.split(".")[-1] if "." in func_name else func_name

        for i, line in enumerate(lines):
            # Match def func_name or class func_name
            stripped = line.lstrip()
            if stripped.startswith(f"def {short_name}") or stripped.startswith(f"class {short_name}"):
                start_idx = i
                indent_level = len(line) - len(stripped)
                break

        if start_idx == -1:
            return None

        # Find the end of the function (next line at same or lower indent level)
        end_idx = start_idx + 1
        while end_idx < len(lines):
            line = lines[end_idx]
            if line.strip() == "":
                end_idx += 1
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent_level and line.strip():
                break
            end_idx += 1

        # Include trailing blank lines that are part of the function block
        while end_idx > start_idx + 1 and lines[end_idx - 1].strip() == "":
            end_idx -= 1

        return "\n".join(lines[start_idx:end_idx])

    @staticmethod
    def _extract_filename(user_input: str) -> str:
        """Extract target filename from user input. Falls back to output.py."""
        # 1. Explicit filename with extension mentioned in input
        # Longer extensions first to avoid partial matches (e.g. .h before .html)
        m = re.search(r'[\w\-]+\.(?:html|yaml|yml|json|toml|java|cpp|css|py|js|ts|go|rs|sh|c|h)\b', user_input)
        if m:
            return m.group(0)

        # 2. Detect target language/filetype from user input → pick correct extension
        ext = _detect_extension(user_input)

        # 3. Chinese/English patterns: "写个XX" / "create XX" → derive filename
        cn_patterns = [
            (r'写(?:个|一个)?(\w+)函数', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)接口', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)脚本', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)类', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)页面', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)组件', lambda m: m.group(1)),
            (r'创建(?:个|一个)?(\w+)文件', lambda m: m.group(1)),
            (r'生成(?:个|一个)?(\w+)代码', lambda m: m.group(1)),
        ]
        for pat, extractor in cn_patterns:
            m = re.search(pat, user_input)
            if m:
                name = extractor(m)
                if name.isascii() and name.isalnum():
                    return f"{name.lower()}{ext}"

        # 4. English patterns
        en_patterns = [
            r'(?:create|write|make|build|generate)\s+(?:a\s+)?(\w+)',
            r'(?:implement|code)\s+(?:a\s+)?(\w+)',
        ]
        for pat in en_patterns:
            m = re.search(pat, user_input, re.IGNORECASE)
            if m:
                name = m.group(1).lower()
                if name not in ('the', 'a', 'an', 'new', 'simple', 'basic', 'my', 'function', 'file', 'code', 'script', 'program'):
                    return f"{name}{ext}"

        return f"output{ext}"

    @staticmethod
    def _func_in_file(func_name: str, content: str) -> bool:
        """Check if a function/class definition exists in content."""
        # Handle "Class.method" names from AST — strip class prefix
        short_name = func_name.split(".")[-1] if "." in func_name else func_name
        return f"def {short_name}" in content or f"class {short_name}" in content

    @staticmethod
    def _clean_code_output(raw: str) -> str:
        """Strip markdown code blocks, thinking tags, tool-call lines, and explanation text from LLM output."""
        text = raw.strip()
        # Strip <think>...</think> blocks from reasoning models
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        # Remove markdown code blocks (including ones in the middle of output)
        # First handle wrapping ```...```
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        # Also remove any stray ``` lines in the middle (LLM artifact)
        lines = text.split("\n")
        lines = [l for l in lines if l.strip() != "```" and not re.match(r'^```\w*$', l.strip())]
        text = "\n".join(lines)

        # Strip leading explanation text (LLM sometimes outputs analysis before code)
        # Find the first line that looks like actual code
        lines = text.split("\n")
        code_start_patterns = (
            "import ", "from ", "class ", "def ", "async def ", "@",
            "#!", "# ", "\"\"\"", "'''", "if __name__",
        )
        first_code_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and any(stripped.startswith(p) for p in code_start_patterns):
                first_code_idx = i
                break
            # Also accept lines that start with valid Python (variable assignment, etc.)
            if stripped and (
                re.match(r'^[A-Z_][A-Z_0-9]*\s*=', stripped) or  # CONSTANT = ...
                re.match(r'^[a-z_]\w*\s*=', stripped) or  # var = ...
                re.match(r'^\w+\s*:', stripped) or  # type annotation
                stripped.startswith(('"""', "'''", "#!/"))
            ):
                first_code_idx = i
                break

        if first_code_idx > 0:
            lines = lines[first_code_idx:]
            text = "\n".join(lines)

        # Strip trailing explanation text (after the last code line)
        lines = text.split("\n")
        last_code_idx = len(lines) - 1
        for i in range(len(lines) - 1, -1, -1):
            stripped = lines[i].strip()
            if not stripped:
                continue
            # If line contains CJK characters (Chinese explanation mixed in code)
            has_cjk = any(0x4e00 <= ord(c) <= 0x9fff for c in stripped)
            # If line looks like explanation text
            if (has_cjk and not stripped.startswith("#") or
                stripped.startswith(("Note:", "注意", "说明", "修改", "以上", "根据",
                                    "接下来", "主要", "总结", "补充", "需要"))):
                last_code_idx = i - 1
            else:
                break
        if last_code_idx < len(lines) - 1 and last_code_idx >= 0:
            lines = lines[:last_code_idx + 1]
            text = "\n".join(lines)

        # Strip tool-call lines that small models sometimes emit
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip().lower()
            # Skip lines that look like tool calls, not file content
            if stripped.startswith((
                "write_file ", "read_file ", "run_bash ", "cd ", "mkdir ",
                "cat ", "echo ", "touch ",
                "edit ", "delete ", "insert_after ",  # hashline指令残留
            )):
                continue
            cleaned.append(line)
        text = "\n".join(cleaned)

        return text.strip()

    @staticmethod
    def _needs_realtime_warning(user_input: str) -> bool:
        """检测用户输入是否涉及实时数据，用于在无搜索结果时添加防编造警告。"""
        keywords = [
            "天气", "气温", "温度", "weather", "forecast",
            "股价", "股票", "汇率", "价格", "price",
            "新闻", "最新", "最近", "今天", "今日", "本周",
            "news", "latest", "today", "recent",
        ]
        lower = user_input.lower()
        return any(kw in lower for kw in keywords)

    def _log_llm_call(self, ctx: TaskContext, caller: str,
                      prompt: str, system: str, raw_output: str):
        """记录LLM调用到审计日志（通过orchestrator的_audit实例）。"""
        try:
            # 通过ctx找到orchestrator的audit logger
            audit = getattr(ctx, '_audit_logger', None)
            if audit and hasattr(audit, 'log_llm_call'):
                prompt_tokens = len(prompt) // 4 + len(system) // 4  # 粗估
                output_tokens = len(raw_output or '') // 4
                audit.log_llm_call(
                    caller=caller,
                    prompt_tokens=prompt_tokens,
                    prompt_preview=prompt[:500],
                    raw_output=(raw_output or '')[:500],
                    output_tokens=output_tokens,
                    engineering_actions={},
                )
            # DetailedLogger: 完整不截断记录
            detailed = getattr(ctx, '_detailed_logger', None)
            if detailed and detailed.enabled:
                detailed.log_llm(
                    caller=caller,
                    prompt=prompt,
                    system=system,
                    raw_output=raw_output or '',
                    tokens={"input": len(prompt) // 4 + len(system) // 4, "output": len(raw_output or '') // 4},
                )
        except Exception:
            pass  # 非阻塞

    def _run_whole_file(self, ctx: TaskContext, files: list[str]) -> Optional[dict]:
        """whole_file scope：LLM返回完整文件内容，直接write_file，不走apply_patch。
        Fallback for _run_stub_decomposed when decomposition fails."""
        patches = []
        explanation_parts = []

        for fpath in files[:3]:
            if "test" in fpath.lower():
                continue

            # 读取当前文件内容
            if self.tools:
                content = self.tools.read_file(fpath)
            else:
                content = ctx.relevant_code_snippets.get(fpath, "")
            if not content or content.startswith("[ERROR]"):
                continue

            # 构建whole_file prompt
            prompt = (
                f"任务：{ctx.user_input[:300]}\n\n"
                f"当前文件 {fpath} 的内容：\n{content}\n\n"
                f"输出完整的Python文件内容。\n"
                f"从第一行import开始到最后一行。\n"
                f"不要只输出函数，输出整个文件。\n"
                f"不要输出markdown代码块标记。\n"
                f"实现所有pass存根函数，保持已有实现不变。"
            )

            # 注入初始测试失败信息
            initial_failure = getattr(ctx, 'initial_test_failure', '')
            if initial_failure:
                prompt += f"\n\n## 当前测试失败详情\n{initial_failure[-2000:]}\n"

            if ctx.retry_hint:
                prompt += f"\n\n## 重试提示\n{ctx.retry_hint}"

            system = self._build_system(ctx)

            raw = self.llm.generate(prompt=prompt, system=system, max_tokens=4096, temperature=0.0)
            self._log_llm_call(ctx, "generator_whole_file", prompt, system, raw)
            code = self._clean_code_output(raw)
            if not code or code == content:
                continue

            patches.append({
                "file": fpath,
                "content": code,
                "write_mode": "whole_file",
            })
            explanation_parts.append(f"{fpath}:whole_file")

        if not patches:
            logger.warning("Generator: whole_file produced no output")
            return None

        result = {
            "patches": patches,
            "explanation": f"Modified: {', '.join(explanation_parts)}",
        }
        ctx.generator_output = result
        return result

    def _needs_whole_file_scope(self, ctx: TaskContext, files: list[str], funcs: list[str]) -> bool:
        """判断任务是否需要整文件scope（重构/拆分/新增类/小文件逻辑错误）。
        通过测试失败信息和任务描述确定性判断，不调用LLM。"""
        user_input = ctx.user_input.lower()
        initial_failure = getattr(ctx, 'initial_test_failure', '') or ''

        # 也检查verifier_output中的错误详情（retry时initial_failure可能为空）
        verifier_detail = ''
        if ctx.verifier_output:
            verifier_detail = ctx.verifier_output.get('error_detail', '')

        combined_test_info = initial_failure + '\n' + verifier_detail

        # 信号1：测试要求导入新类/函数或新模块
        if 'cannot import name' in combined_test_info:
            return True
        if 'ModuleNotFoundError' in combined_test_info or 'module_exists' in combined_test_info:
            return True

        # 信号2：测试要求代码行数缩短（refactoring test）
        if 'lines after refactoring' in combined_test_info or 'should be <=' in combined_test_info:
            return True

        # 信号3：测试要求调用子函数（extracted methods）
        if 'extracted methods' in combined_test_info or 'calls_subfunctions' in combined_test_info:
            return True

        # 信号4：任务描述明确要求重构/拆分/提取
        refactor_signals = ["重构", "拆分", "提取", "extract", "split", "refactor",
                           "decompose", "reorganize"]
        if any(s in user_input for s in refactor_signals):
            return True

        # 信号5：locator找到的函数名包含refactoring相关测试名
        func_str = ' '.join(funcs).lower()
        if 'refactor' in func_str or 'extract' in func_str or 'split' in func_str:
            return True

        # 信号6：逻辑错误（AssertionError）的单文件任务，统一走whole_file
        # whole_file给LLM完整上下文比hashline更可靠（避免缩进问题）
        # 对32B模型来说token不是瓶颈，完整文件输出更稳定
        if files and self.tools and ctx.gap and hasattr(ctx.gap, 'gap_type'):
            from kaiwu.core.gap_detector import GapType
            if ctx.gap.gap_type == GapType.LOGIC_ERROR:
                # 单文件任务（非test文件≤3个）统一走whole_file
                non_test_files = [f for f in files if "test" not in f.lower()]
                if non_test_files:
                    return True

        # 信号7：文件中只有一个大类/函数，且测试中有refactor相关名称
        if files and self.tools:
            for fpath in files[:1]:
                if "test" in fpath.lower():
                    continue
                content = self.tools.read_file(fpath)
                if content and not content.startswith("[ERROR]"):
                    lines = content.split('\n')
                    if len(lines) > 60 and ('refactor' in combined_test_info.lower() or
                                            'shorter' in combined_test_info.lower()):
                        return True

        return False

    def _run_whole_file_refactor(self, ctx: TaskContext, files: list[str]) -> Optional[dict]:
        """整文件scope重构：LLM看到完整文件+测试失败，输出完整修改后的文件。
        与_run_whole_file不同：prompt针对重构/bug修复而非存根实现。"""
        patches = []
        explanation_parts = []

        # 收集所有非test文件的内容（用于多文件上下文注入）
        all_file_contents = {}
        source_files = []
        for fpath in files[:5]:
            if "test" in fpath.lower():
                continue
            source_files.append(fpath)
            if self.tools:
                c = self.tools.read_file(fpath)
                if c and not c.startswith("[ERROR]"):
                    all_file_contents[fpath] = c

        # D步骤：失败测试归因到源文件（context准备的核心）
        initial_failure = getattr(ctx, 'initial_test_failure', '') or ''
        from kaiwu.core.test_parser import attribute_failures_to_files
        file_failures = attribute_failures_to_files(initial_failure, source_files)

        # 只处理有归因失败的文件（没有相关失败的文件不要让LLM修改）
        files_to_process = []
        for fpath in files[:5]:
            if "test" in fpath.lower():
                continue
            if fpath in file_failures and file_failures[fpath]:
                files_to_process.append(fpath)
        # 如果归因没有结果（可能是traceback格式不匹配），fallback到处理所有文件
        if not files_to_process:
            files_to_process = [f for f in files[:3] if "test" not in f.lower()]

        for fpath in files_to_process:

            if self.tools:
                content = self.tools.read_file(fpath)
            else:
                content = ctx.relevant_code_snippets.get(fpath, "")
            if not content or content.startswith("[ERROR]"):
                continue

            # 构建跨文件上下文：用AST提取其他文件的接口签名（节省token）
            other_files_ctx = ""
            for other_fpath, other_content in all_file_contents.items():
                if other_fpath != fpath:
                    interface = self._extract_interface(other_content)
                    if interface:
                        other_files_ctx += f"\n### 相关文件 {other_fpath}（只读，不要修改）\n{interface}\n"

            prompt = (
                f"任务：{ctx.user_input[:500]}\n\n"
                f"当前文件 {fpath} 的完整内容：\n{content}\n\n"
            )

            # 注入其他文件接口上下文（多文件任务时）
            if other_files_ctx:
                prompt += f"## 相关文件接口（只读参考，帮助理解依赖关系）\n{other_files_ctx}\n\n"

            prompt += (
                f"根据任务要求和测试失败信息，修改 {fpath} 这个文件。\n"
                f"输出修改后的完整文件内容（从第一行到最后一行）。\n"
                f"不要输出markdown代码块标记。\n"
                f"保持所有已通过测试的功能不变，只修复失败的部分。"
            )

            # C步骤增强：从stack trace提取故障函数，告诉LLM重点修哪里
            from kaiwu.core.test_parser import extract_fault_functions
            fault_funcs = extract_fault_functions(initial_failure, [fpath])
            if fault_funcs:
                hints = [f"  - {ff['function']}() (line {ff['line']})" for ff in fault_funcs[:5]]
                prompt += f"\n\n## Bug定位（重点修复这些函数）\n" + "\n".join(hints) + "\n"

            # D步骤：只注入当前文件相关的失败测试（不是全部FAILURES）
            # 失败批次控制：每次最多给3个失败，避免LLM被太多失败淹没输出分析文本
            file_specific_failures = file_failures.get(fpath, [])
            unattributed = file_failures.get('__unattributed__', [])
            if file_specific_failures:
                # 最多3个失败，让LLM专注修复而非分析
                batch = file_specific_failures[:3]
                failures_text = "\n\n".join(batch)
                prompt += f"\n\n## 当前文件 {fpath} 需要修复的测试失败（共{len(file_specific_failures)}个，先修这{len(batch)}个）\n{failures_text}\n"
            elif unattributed:
                batch = unattributed[:3]
                prompt += f"\n\n## 需要修复的测试失败\n" + "\n\n".join(batch)
            elif initial_failure:
                # 没有归因结果时fallback到原始行为（取末尾）
                prompt += f"\n\n## 当前测试失败详情\n{initial_failure[-2500:]}\n"

            if ctx.retry_hint:
                prompt += f"\n\n## 重试提示\n{ctx.retry_hint}"

            # 多文件任务用极简system prompt（防止LLM输出分析文本）
            # 单文件任务用标准system prompt（更稳定）
            if len(all_file_contents) > 1:
                system = "You are a code editor. Output ONLY the complete modified file content. No explanations, no markdown fences, no comments about changes. Start with the first line of code."
            else:
                system = self._build_system(ctx)

            # 代码生成：使用temperature=0.0确保稳定输出
            best_code = None
            raw = self.llm.generate(prompt=prompt, system=system, max_tokens=4096, temperature=0.0)
            self._log_llm_call(ctx, "generator_whole_file_refactor", prompt, system, raw)
            candidate = self._clean_code_output(raw)
            if candidate and candidate != content and self._is_valid_syntax(candidate):
                best_code = candidate

            if not best_code:
                continue
            code = best_code

            patches.append({
                "file": fpath,
                "content": code,
                "write_mode": "whole_file",
            })
            explanation_parts.append(f"{fpath}:refactor")

        if not patches:
            logger.warning("Generator: whole_file_refactor produced no output")
            return None

        result = {
            "patches": patches,
            "explanation": f"Modified: {', '.join(explanation_parts)}",
        }
        ctx.generator_output = result
        return result

    @staticmethod
    def _extract_interface(content: str) -> str:
        """用AST提取类、方法、函数的签名（不含函数体），让LLM知道接口。"""
        import ast
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # 解析失败，返回前30行作为fallback
            return '\n'.join(content.split('\n')[:30])

        lines = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                lines.append(f"class {node.name}:")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        try:
                            args = ast.unparse(item.args)
                            returns = f" -> {ast.unparse(item.returns)}" if item.returns else ""
                            lines.append(f"    def {item.name}({args}){returns}: ...")
                        except Exception:
                            lines.append(f"    def {item.name}(...): ...")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                try:
                    args = ast.unparse(node.args)
                    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                    lines.append(f"def {node.name}({args}){returns}: ...")
                except Exception:
                    lines.append(f"def {node.name}(...): ...")
            elif isinstance(node, ast.Assign):
                # 顶层常量/变量
                try:
                    lines.append(ast.unparse(node))
                except Exception:
                    pass

        return '\n'.join(lines) if lines else content[:500]

    def _run_incremental_fix(self, ctx: TaskContext, files: list[str]) -> Optional[dict]:
        """高通过率时的增量修复：在best_state基础上用hashline只修剩余失败的测试。
        避免whole_file重写导致退步。"""
        patches = []
        explanation_parts = []

        # 获取剩余失败的测试信息
        failing_info = ""
        if ctx.verifier_output:
            failing_info = ctx.verifier_output.get("error_detail", "")
        if not failing_info:
            failing_info = getattr(ctx, 'initial_test_failure', '') or ''

        for fpath in files[:3]:
            if "test" in fpath.lower():
                continue

            # 读取best_state的文件内容（不是磁盘上的原始文件）
            if fpath in ctx.best_code_snapshot:
                content = ctx.best_code_snapshot[fpath]
            elif self.tools:
                content = self.tools.read_file(fpath)
            else:
                continue
            if not content or content.startswith("[ERROR]"):
                continue

            # 用hashline在best_state基础上做增量修复
            try:
                from kaiwu.tools.hashline import add_anchors, parse_anchor_edits, apply_anchor_edits
            except ImportError:
                continue

            anchored = add_anchors(content)
            prompt = (
                f"你是代码修复专家。以下代码已经通过了大部分测试，但还有几个测试失败。\n"
                f"用锚点编辑指令修复剩余的失败测试，不要破坏已通过的测试。\n\n"
                f"任务：{ctx.user_input[:300]}\n\n"
                f"当前代码（来自 {fpath}，每行格式: 行号|哈希|内容）：\n{anchored}\n\n"
                f"剩余失败的测试：\n{failing_info[-2000:]}\n\n"
                f"只输出编辑指令，每行一条，格式：\n"
                f"  EDIT 行号|哈希| → 新内容\n"
                f"  DELETE 行号|哈希|\n"
                f"  INSERT_AFTER 行号|哈希| → 新行内容\n\n"
                f"规则：\n"
                f"1. 只修改导致测试失败的部分，≤15条指令\n"
                f"2. 不要破坏已通过的测试\n"
                f"3. 哈希必须与上面代码中的完全一致\n"
                f"4. 保持原始缩进风格\n"
            )

            if ctx.retry_hint:
                prompt += f"\n## 重试提示\n{ctx.retry_hint}"

            system = self._build_system(ctx)
            orig_lines = content.count('\n') + 1
            hashline_tokens = min(2048, max(1024, orig_lines * 25))
            raw = self.llm.generate(prompt=prompt, system=system, max_tokens=hashline_tokens, temperature=0.0)
            self._log_llm_call(ctx, "generator_incremental_fix", prompt, system, raw)

            if not raw or not raw.strip():
                continue

            edits = parse_anchor_edits(raw)
            if not edits:
                continue

            modified, errors = apply_anchor_edits(content, edits)
            if errors or modified == content:
                continue

            patches.append({
                "file": fpath,
                "content": modified,
                "write_mode": "whole_file",
            })
            explanation_parts.append(f"{fpath}:incremental_fix")

        if not patches:
            # Fallback: hashline失败，用whole_file但基于当前磁盘文件（best_state）
            # 不用_run_whole_file_refactor（那个读原始文件），而是直接读磁盘当前状态
            logger.info("Incremental hashline fix failed, trying targeted whole_file fix on current state")
            return self._run_targeted_fix(ctx, files)

        result = {
            "patches": patches,
            "explanation": f"Modified: {', '.join(explanation_parts)}",
        }
        ctx.generator_output = result
        return result

    def _run_targeted_fix(self, ctx: TaskContext, files: list[str]) -> Optional[dict]:
        """基于当前磁盘文件（best_state）做targeted fix。
        与whole_file_refactor不同：读取的是当前磁盘状态（已部分修复），
        prompt强调只修剩余失败测试，不要改动已通过的部分。"""
        patches = []
        explanation_parts = []

        failing_info = ""
        if ctx.verifier_output:
            failing_info = ctx.verifier_output.get("error_detail", "")
        if not failing_info:
            failing_info = getattr(ctx, 'initial_test_failure', '') or ''

        for fpath in files[:3]:
            if "test" in fpath.lower():
                continue

            # 读取当前磁盘文件（verifier不再rollback，这就是best_state）
            if self.tools:
                content = self.tools.read_file(fpath)
            else:
                continue
            if not content or content.startswith("[ERROR]"):
                continue

            # D步骤增强：提取失败测试的源代码（让LLM看到expected值）
            test_code_snippet = self._extract_failing_test_code(ctx, fpath)

            # C步骤增强：从stack trace提取故障函数（精确到函数级）
            from kaiwu.core.test_parser import extract_fault_functions
            fault_funcs = extract_fault_functions(failing_info, [fpath])
            fault_hint = ""
            if fault_funcs:
                hints = [f"  - {ff['function']}() (line {ff['line']}, {ff['count']}个测试指向此处)" for ff in fault_funcs[:5]]
                fault_hint = "## Bug定位（从stack trace确定性提取）\n" + "\n".join(hints) + "\n\n"

            prompt = (
                f"以下代码已经通过了大部分测试（{ctx.best_tests_passed}个），但还有几个测试失败。\n"
                f"请只修复导致失败的bug，不要改动已通过测试的代码。\n\n"
                f"任务：{ctx.user_input[:300]}\n\n"
            )

            # 注入故障函数定位（最关键的信息）
            if fault_hint:
                prompt += fault_hint

            prompt += f"当前文件 {fpath} 的完整内容：\n{content}\n\n"

            # 注入失败测试的源代码（比error output更有用）
            if test_code_snippet:
                prompt += f"## 失败测试的源代码（展示期望行为）\n{test_code_snippet}\n\n"

            prompt += (
                f"## 测试错误输出\n{failing_info[-1500:]}\n\n"
                f"输出修改后的完整文件内容。\n"
                f"关键要求：\n"
                f"- 重点修复上面定位到的函数中的bug\n"
                f"- 不要改动已通过测试的代码\n"
                f"- 不要输出markdown代码块标记\n"
            )

            if ctx.retry_hint:
                prompt += f"\n## 重试提示\n{ctx.retry_hint}"

            system = self._build_system(ctx)
            raw = self.llm.generate(prompt=prompt, system=system, max_tokens=4096, temperature=0.0)
            self._log_llm_call(ctx, "generator_targeted_fix", prompt, system, raw)
            code = self._clean_code_output(raw)
            if not code or code == content:
                continue

            patches.append({
                "file": fpath,
                "content": code,
                "write_mode": "whole_file",
            })
            explanation_parts.append(f"{fpath}:targeted_fix")

        if not patches:
            return None

        result = {
            "patches": patches,
            "explanation": f"Modified: {', '.join(explanation_parts)}",
        }
        ctx.generator_output = result
        return result

    def _extract_failing_test_code(self, ctx: TaskContext, source_fpath: str) -> str:
        """从测试文件中提取失败测试的源代码，让LLM看到expected值。"""
        import os
        import re as _re

        # 找到测试文件
        project_root = ctx.project_root
        test_files = []
        if self.tools:
            dir_contents = self.tools.list_dir(project_root)
            test_files = [f for f in dir_contents if 'test' in f.lower() and f.endswith('.py')]

        if not test_files:
            return ""

        # 读取测试文件
        test_content = ""
        for tf in test_files[:1]:
            if self.tools:
                test_content = self.tools.read_file(tf)
                if test_content and not test_content.startswith("[ERROR]"):
                    break

        if not test_content:
            return ""

        # 从verifier_output中获取失败测试名
        failing_tests = []
        if ctx.verifier_output:
            failing_tests = ctx.verifier_output.get("failed_tests", [])
        if not failing_tests:
            error_detail = ctx.verifier_output.get("error_detail", "") if ctx.verifier_output else ""
            failing_tests = _re.findall(r'FAILED\s+[\w/.]+::([\w:]+)', error_detail)

        if not failing_tests:
            return ""

        # 提取失败测试函数的源代码
        snippets = []
        for test_name in failing_tests[:5]:
            # 从test_name中提取函数名（可能是TestClass::test_method格式）
            func_name = test_name.split("::")[-1] if "::" in test_name else test_name
            func_name = func_name.split(".")[-1] if "." in func_name else func_name

            # 在测试文件中找到这个函数
            pattern = f"def {func_name}"
            lines = test_content.split("\n")
            start_idx = -1
            for i, line in enumerate(lines):
                if pattern in line:
                    start_idx = i
                    break

            if start_idx == -1:
                continue

            # 提取函数体（到下一个def或class或文件结束）
            end_idx = start_idx + 1
            indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
            while end_idx < len(lines):
                line = lines[end_idx]
                if line.strip() and not line.startswith(" " * (indent + 1)) and not line.strip().startswith("#"):
                    if line.strip().startswith(("def ", "class ")):
                        break
                end_idx += 1
                if end_idx - start_idx > 15:  # 最多15行
                    break

            snippet = "\n".join(lines[start_idx:end_idx])
            snippets.append(snippet)

        return "\n\n".join(snippets[:3])  # 最多3个测试函数

    def _run_stub_decomposed(self, ctx: TaskContext, files: list[str], funcs: list[str]) -> Optional[dict]:
        """
        Sub-task decomposition for stub tasks.
        Instead of asking LLM to implement all pass functions at once,
        decompose into per-function subtasks with independent bounded context.
        Each function gets its own LLM call with only its code + relevant tests.
        Falls back to _run_whole_file if decomposition fails.
        """
        patches = []
        explanation_parts = []

        for fpath in files[:3]:
            if "test" in fpath.lower():
                continue

            # Read file content
            if self.tools:
                content = self.tools.read_file(fpath)
            else:
                content = ctx.relevant_code_snippets.get(fpath, "")
            if not content or content.startswith("[ERROR]"):
                continue

            # Find all stub functions (pass/... body)
            stub_funcs = self._find_stub_functions(content)
            if not stub_funcs:
                continue

            # If only 1-2 stubs, or file is small, use whole_file (simpler)
            if len(stub_funcs) <= 2 or len(content.split("\n")) < 50:
                result = self._run_whole_file(ctx, [fpath])
                if result:
                    return result
                continue

            # ── Per-function decomposition ──
            initial_failure = getattr(ctx, 'initial_test_failure', '')
            structured_failures = (ctx.verifier_output or {}).get("structured_failures", [])
            if not structured_failures and initial_failure:
                from kaiwu.core.test_parser import parse_test_failures
                structured_failures = parse_test_failures(initial_failure)

            file_patches = []
            for func_name, func_code in stub_funcs:
                # Build bounded context for this single function
                relevant_tests = self._filter_relevant_failures(
                    structured_failures, func_name, fpath
                )

                test_info = ""
                if relevant_tests:
                    lines = []
                    for f in relevant_tests[:3]:
                        name = f.get("test_name", "?")
                        expected = f.get("expected", "")
                        actual = f.get("actual", "")
                        snippet = f.get("snippet", "")
                        if expected and actual:
                            lines.append(f"- {name}: 期望 {expected}，实际 {actual}")
                        elif snippet:
                            lines.append(f"- {name}: {snippet[:120]}")
                        else:
                            lines.append(f"- {name}")
                    test_info = "\n\n## 相关测试\n" + "\n".join(lines)

                prompt = (
                    f"任务：{ctx.user_input[:200]}\n\n"
                    f"实现以下函数（来自 {fpath}）：\n"
                    f"```\n{func_code}\n```\n\n"
                    f"只输出实现后的完整函数代码（从def开始）。\n"
                    f"保持原始缩进和签名不变，只替换pass为实现。\n"
                    f"纯代码，无markdown，无解释。"
                    f"{test_info}"
                )

                if ctx.search_results:
                    prompt += f"\n\n参考资料：\n{ctx.search_results[:500]}"

                system = self._build_system(ctx)

                raw = self.llm.generate(prompt=prompt, system=system, max_tokens=2048, temperature=0.0)
                self._log_llm_call(ctx, f"generator_stub_{func_name}", prompt, system, raw)
                modified = self._clean_code_output(raw)

                if modified and modified.strip() != func_code.strip():
                    modified = modified.strip("\n")
                    modified = self._align_indentation(func_code, modified)
                    file_patches.append({
                        "file": fpath,
                        "original": func_code,
                        "modified": modified,
                    })
                    explanation_parts.append(f"{fpath}:{func_name}")

            if file_patches:
                patches.extend(file_patches)

        if not patches:
            logger.debug("Stub decomposition produced no patches, falling back to whole_file")
            return self._run_whole_file(ctx, files)

        result = {
            "patches": patches,
            "explanation": f"Stub impl: {', '.join(explanation_parts)}",
        }
        ctx.generator_output = result
        return result

    @staticmethod
    def _find_stub_functions(content: str) -> list[tuple[str, str]]:
        """
        Find all stub functions (body is just 'pass', '...', or 'return None') in file content.
        Returns list of (func_name, full_function_code) tuples.
        """
        lines = content.split("\n")
        stubs = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            if stripped.startswith("def "):
                func_name = stripped[4:].split("(")[0].strip()
                indent_level = len(line) - len(stripped)
                start_idx = i

                # Find end of function
                end_idx = i + 1
                while end_idx < len(lines):
                    l = lines[end_idx]
                    if l.strip() == "":
                        end_idx += 1
                        continue
                    current_indent = len(l) - len(l.lstrip())
                    if current_indent <= indent_level and l.strip():
                        break
                    end_idx += 1

                func_code = "\n".join(lines[start_idx:end_idx]).rstrip()

                # Check if body is stub (skip docstrings and comments)
                in_docstring = False
                clean_body = []
                for bl in lines[start_idx + 1:end_idx]:
                    s = bl.strip()
                    if not s:
                        continue
                    if s.startswith('"""') or s.startswith("'''"):
                        if s.count('"""') >= 2 or s.count("'''") >= 2:
                            continue  # single-line docstring
                        in_docstring = not in_docstring
                        continue
                    if in_docstring:
                        continue
                    if s.startswith("#"):
                        continue
                    clean_body.append(s)

                is_stub = (
                    len(clean_body) == 0 or
                    (len(clean_body) == 1 and clean_body[0] in (
                        "pass", "...", "return None",
                        "raise NotImplementedError",
                        "raise NotImplementedError()",
                    ))
                )

                if is_stub:
                    stubs.append((func_name, func_code))

                i = end_idx
            else:
                i += 1

        return stubs
