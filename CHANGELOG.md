# Changelog

All notable changes to KWCode are documented here.

---

## [1.0.0] - 2026-04-29

### Architecture: 元专家体系定稿

KWCode 的专家系统从"按业务领域枚举"升级为"按原子能力分层 + 领域知识注入"。

**5 个元专家（原子能力层，固定不变）：**

| 元专家 | 能力 | 文件 |
|--------|------|------|
| Locator | 代码定位（BM25+AST调用图） | `experts/locator.py` |
| Generator | 代码生成/编辑 | `experts/generator.py` |
| Verifier | 测试验证（pytest） | `experts/verifier.py` |
| Debugger | 运行时调试（sys.settrace） | `experts/debug_subagent.py` |
| Reviewer | 需求对齐审查 | `experts/reviewer.py` |

**15 个领域知识（SKILL.md 注入层，可扩展）：**

BugFix · FastAPI · TestGen · API · DeepSeekAPI · Docstring · MyBatis · OfficeDocx · OfficePptx · OfficeXlsx · Refactor · SpringBoot · SQLOpt · TypeHint · UniApp

领域知识不改变流水线结构，只注入 Generator 的 system_prompt。

### Added

- **Reviewer 元专家** (`experts/reviewer.py`)：Verifier 通过后用 LLM 对比用户意图和实际变更，判断需求是否对齐
- **SKILL.md 渐进式加载**：全部 15 个专家从 YAML 升级为 SKILL.md 目录格式
  - Level 1（Gate）：name + keywords，~100 token/专家
  - Level 2（Generator）：完整领域知识，仅命中专家加载
  - Level 3（on-demand）：确定性脚本，不进 LLM context
- **DAG TaskCompiler** (`core/task_compiler.py`)：串行+并行多任务调度
- **`/multi` 命令**：CLI 多任务模式（分号并行、箭头串行、交互式混合）
- **Debug Subagent** (`experts/debug_subagent.py`)：verifier 失败后 sys.settrace 捕获运行时变量
- **Prompt Optimizer** (`flywheel/prompt_optimizer.py`)：飞轮优化 YAML/SKILL.md 的领域知识内容
- **Cross-Encoder 重排** (`search/reranker.py`)：可选搜索结果精排
- **Reflexion 持久化** (`memory/pattern_md.py`)：REFLECTION.md 结构化记录 + /plan 风险注入

### Changed

- 专家格式从 flat YAML 升级为 SKILL.md 目录（向后兼容已移除，全量迁移）
- 版本号从 0.7.0 → 1.0.0
- 测试数量：282 → 311

### Removed

- 15 个旧 `.yaml` 专家文件（已全部转为 SKILL.md 目录）
- Python 专家系统（ExpertBase、BugFixExpert.py、SelfImprovingOptimizer）— 方向错误，v0.8.0 加入后 v0.9.0 移除

### Architecture Decisions

- **元专家按原子能力分，不按业务领域分**：研究证明 MoE 路由反映隐状态几何结构而非领域专业性（arXiv:2604.09780）
- **领域知识是注入层，不是独立流水线**：所有任务走同一条 Locator→Generator→Verifier 管线，区别只在 system_prompt
- **渐进式加载解决噪音问题**：Gate 只看 metadata（~1500 token/15专家），不全量加载所有知识
- **Reviewer 非阻塞**：审查结果不回滚代码，只提示用户注意 gap

---

## [0.9.0] - 2026-04-29

### Added
- DAG TaskCompiler + /multi 命令
- Debug Subagent（基于 Debug2Fix 论文）
- Prompt Optimizer（优化 YAML system_prompt）
- Reflexion 持久化 + Cross-Encoder 重排

### Removed
- Python 专家系统（ExpertBase、BugFixExpert.py、SelfImprovingOptimizer）
- Registry Python 专家加载逻辑

---

## [0.7.0] - 2026-04-29

### Added
- UI 全面优化（spinner、结果摘要、重影大字 Header）
- 意图感知搜索
- 搜索模块重构（四级提取管道）
- P2 三大功能（模型自适应、飞轮通知、价值仪表盘）
- P1 四大功能（KWCODE.md、/plan、Checkpoint、DocReader）
- MVP 核心流水线（Gate→Locator→Generator→Verifier→Search）

---

## References

| 论文 | 对 KWCode 的影响 |
|------|-----------------|
| Agentless (ICSE 2025) | 整体确定性流水线架构 |
| CodeCompass (2026) | AST 调用图定位 |
| Debug2Fix (ICML 2026) | Debug Subagent |
| LLMCompiler (ICML 2024) | DAG 任务调度 |
| Reflexion (NeurIPS 2023) | 失败模式持久化 |
| SICA (2025) | Prompt 自动优化 |
| GitHub Copilot Atomic Skills (2025) | 5 原子能力分层 |
| SWE-Skills-Bench (2026) | 80% 泛泛 skill 无效，具体知识才有效 |
| MoE Routing Geometry (2026) | 专家不按领域分，按能力分 |
| Agent Skills Progressive Disclosure (Anthropic 2026) | 渐进式加载架构 |
