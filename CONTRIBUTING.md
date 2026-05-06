# KWCode 贡献指南

感谢你的贡献。请在提交 PR 前阅读本文档。

---

## 快速开始

```bash
git clone https://github.com/val1813/kwcode.git
cd kwcode
pip install -e ".[dev]"
python -m pytest kaiwu/tests/ -v --ignore=kaiwu/tests/bench_tasks
# 全部绿才能提 PR
```

---

## 架构红线（违反即拒绝）

kwcode 的核心设计原则是**确定性流水线**，任何 PR 不得违反：

| 红线 | 说明 |
|------|------|
| **RED-1** | Gate 必须输出结构化 JSON，不得用字符串解析分类结果 |
| **RED-2** | LLM 只做分类和生成，不得在流水线 step 之间让 LLM 决定下一步 |
| **RED-3** | 每个专家有独立上下文窗口，不得继承上一个专家的对话历史 |
| **RED-4** | 新增依赖必须离线可用，不得引入需要外部服务才能运行的包 |
| **RED-5** | 重试次数必须有硬上限，不得无限循环 |

**一票否决的改动类型**：
- 引入向量数据库（Mem0、Chroma、Pinecone、Weaviate 等）
- 引入企业级安全库（LLM-Guard、Guardrails 等）
- 引入需要云服务的依赖（非离线可用）
- 在确定性流水线的 step 之间插入 LLM routing 调用
- 多 Agent 并行框架（现阶段串行流水线，不做并行）
- 自动修改 Gate 路由规则的逻辑（漂移难追踪）

---

## 欢迎的贡献类型

按优先级排序：

**P0 — 最欢迎**
- 新增预置专家（bugfix/refactor/testgen 等领域的 SKILL.md + YAML）
- 修复已知 bug（附上能复现 bug 的测试用例）
- 多语言 AST 支持（JS/TS/Go/Rust/Java 调用图）
- 性能优化（Locator 定位速度、ContextPruner 压缩质量）

**P1 — 欢迎**
- Verifier 结构化输出改进（error_type 分类更精准）
- Experience Replay / trajectory_collector 检索能力
- Session 连贯性改进（System Reminders、RULES.md）
- README / 文档改进（中英文均可）
- CI/CD 改进（GitHub Actions、测试覆盖率）

**P2 — 需讨论后再做**
- 新增 CLI 命令（先开 Issue 讨论）
- Gate 分类逻辑改动（影响所有任务路由）
- Orchestrator 流程改动（影响核心流水线）
- 新增 llm backend 支持

---

## PR 标准

### 必须满足

**1. 测试全绿**
```bash
python -m pytest kaiwu/tests/ -v --ignore=kaiwu/tests/bench_tasks
```
所有现有测试必须通过，不得删除已有测试。

**2. 新功能必须有测试**
- 改动了 `kaiwu/core/`（gate/orchestrator/verifier）→ 必须有对应测试
- 改动了 `kaiwu/experts/` → 必须有对应测试
- 改动了 `kaiwu/flywheel/` → 必须有对应测试
- 只改 README / .gitignore / 文档 → 不需要测试

**3. 新增依赖需说明**
在 PR 描述里说明：
- 为什么需要这个包
- 是否离线可用
- 包大小和主要依赖

**4. 文档和实现同步**
README 里提到的功能必须已经实现，不得在文档里描述未实现的功能。

### PR 描述模板

```
## 改动内容
<!-- 一句话说明这个 PR 做了什么 -->

## 改动类型
- [ ] Bug 修复
- [ ] 新增专家 / SKILL.md
- [ ] 性能优化
- [ ] 文档 / CI 改进
- [ ] 其他（请说明）

## 测试
- [ ] 现有测试全部通过
- [ ] 新增了对应测试
- [ ] 只改文档，无需测试

## 新增依赖（如有）
| 包名 | 版本 | 用途 | 离线可用 |
|------|------|------|---------|
|      |      |      |         |

## 验证方式
<!-- 说明如何验证这个改动有效 -->
```

---

## 新增专家（最简单的贡献方式）

1. 复制 `kaiwu/builtin_experts/bugfix/` 目录结构
2. 编辑 `SKILL.md`（领域知识，越详细越好）
3. 用真实项目测试 ≥ 5 个任务，通过率 ≥ 80%
4. 在 PR 里附上测试结果截图

急需认领的专家：
- `Vue3Expert` / `ReactExpert`
- `DjangoExpert` / `FastAPIExpert`
- `GoGinExpert` / `RustActixExpert`
- `K8sExpert` / `DockerExpert`
- `MySQLExpert` / `RedisExpert`

---

## 代码风格

- Python 3.10+，类型注解尽量完整
- 非阻塞路径的异常必须 `logger.debug/warning`，不得静默吞掉
- 新模块加模块级 docstring，说明设计意图
- 中文注释可以，英文也可以，同一文件保持一致

---

## 开 Issue 还是直接 PR？

| 情况 | 建议 |
|------|------|
| 发现 bug | 直接 PR（附复现步骤和测试） |
| 新增专家 | 直接 PR |
| 改动 Gate / Orchestrator 逻辑 | 先开 Issue 讨论 |
| 新增 CLI 命令 | 先开 Issue 讨论 |
| 不确定方向对不对 | 先开 Issue |

---

## 行为准则

- 代码审查的反馈是针对代码，不是针对人
- 中英文交流均可
- 不接受的 PR 会说明原因，欢迎根据反馈修改后重新提交
