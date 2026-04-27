# KwQode - 本地模型 Coding Agent

> 中国开发者的本地 coding agent——Windows 打开就能用，数据不出网，越用越懂你的项目。

## 特性

- **Windows/Mac/Linux 原生支持** — Windows 下 cmd/PowerShell 直接运行，无需 WSL
- **数据完全本地** — 代码和对话不出本机网络，适合企业内网和涉密项目
- **支持国产模型** — DeepSeek、Qwen、Gemma 等，通过 Ollama 一键管理
- **确定性专家流水线** — Gate 路由 + 专家流水线，小模型也能高效完成复杂任务
- **12 个预置专家** — API、BugFix、重构、测试生成、FastAPI、SpringBoot、MyBatis 等
- **自动专家飞轮** — 用得越多，专家越精准，自动从成功轨迹中学习
- **搜索增强** — DuckDuckGo 搜索，零 API key，自动为 LLM 补充上下文
- **项目记忆** — KAIWU.md 记录项目架构和偏好，跨会话保持上下文
- **MCP 协议支持** — 可作为 MCP Server 接入 Claude Code、Cursor 等 IDE

## 快速开始

### 一键安装

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

**Mac/Linux:**

```bash
chmod +x install.sh && ./install.sh
```

### 手动安装

```bash
# 1. 安装 KwQode
pip install kaiwu
# 国内网络慢可用清华镜像
pip install kaiwu -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 安装 Ollama（本地模型推理引擎）
# Windows/Mac: https://ollama.com/download
# Linux: curl -fsSL https://ollama.com/install.sh | sh

# 3. 拉取模型（按显存选择）
ollama pull qwen3:8b      # 8GB+ 显存推荐
ollama pull qwen3:14b     # 16GB+ 显存推荐
ollama pull gemma3:4b     # 4GB 显存或 CPU

# 4. 初始化项目
cd your-project
kwqode init
```

## 使用方法

### 基础用法

```bash
# 交互模式（REPL）
kwqode

# 单次任务
kwqode "修复登录接口的空指针异常"
kwqode "给 UserService 加单元测试"
kwqode "把这个函数重构成策略模式"
```

### CLI 参数

```bash
kwqode [任务] [选项]

选项:
  -m, --model TEXT       Ollama 模型名称（默认 qwen3-8b）
  --model-path TEXT      本地 GGUF 模型路径（不用 Ollama）
  --ollama-url TEXT      Ollama 地址（默认 http://localhost:11434）
  -d, --project TEXT     项目根目录（默认当前目录）
  -p, --plan             先显示执行计划，确认后再执行
  -v, --verbose          显示详细日志
```

### 交互模式命令

在 REPL 中可用的斜杠命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/memory` | 查看项目记忆 (KAIWU.md) |
| `/init` | 初始化 KAIWU.md |
| `/model qwen3:14b` | 切换模型 |
| `/cd /path/to/project` | 切换项目目录 |
| `/experts` | 列出已注册专家 |
| `/plan` | 下一个任务先显示计划再执行 |
| `/exit` | 退出 |

### 子命令

```bash
kwqode init              # 初始化项目记忆
kwqode status            # 查看模型/专家/连接状态
kwqode memory            # 查看 KAIWU.md 内容
kwqode serve-mcp         # 启动 MCP Server（stdio 模式）
```

### 专家管理

```bash
kwqode expert list                # 列出所有专家
kwqode expert info APIExpert      # 查看专家详情
kwqode expert create my-expert    # 创建自定义专家模板
kwqode expert export APIExpert    # 导出为 .kwx 包
kwqode expert install ./my.kwx    # 安装专家包
kwqode expert remove my-expert    # 删除专家
```

## 架构

```
用户输入（CLI / MCP）
    │
    ▼
  Gate（单次 LLM 调用，JSON 路由）
    │  ├─ 匹配注册专家 → 专家流水线
    │  └─ 通用分类 → 内置流水线
    ▼
  ┌──────────────────────────────────────┐
  │ locator_repair: Locator→Generator→Verifier │
  │ codegen:        Generator→Verifier          │
  │ refactor:       Locator→Generator→Verifier  │
  │ doc:            Generator                    │
  │ office:         OfficeHandler                │
  └──────────────────────────────────────┘
    │
    ▼ 失败重试（最多 3 次，2 次失败触发搜索增强）
  SearchAugmentor（DuckDuckGo → 正文提取 → LLM 压缩）
    │
    ▼
  KAIWU.md 记忆写入 + 专家飞轮学习
```

**核心模块：**

| 模块 | 职责 |
|------|------|
| `core/gate.py` | 任务分类路由，匹配专家或内置流水线 |
| `experts/locator.py` | 两阶段定位：文件级 → 函数级（AST + 符号索引） |
| `experts/generator.py` | 代码生成，从文件读 original，LLM 只生成 modified |
| `experts/verifier.py` | 语法检查 + pytest 验证 |
| `experts/search_augmentor.py` | 6 步搜索增强流水线 |
| `llm/llama_backend.py` | Ollama + llama.cpp 双后端 |
| `memory/kaiwu_md.py` | 项目记忆持久化 |
| `registry/` | 专家注册、打包、飞轮 |
| `mcp/router_mcp.py` | MCP Server 协议适配 |

## 支持的模型

| 模型 | 显存需求 | 推荐场景 | 备注 |
|------|----------|----------|------|
| `qwen3:14b` | 16GB+ | 日常开发首选 | 中文理解最佳 |
| `qwen3:8b` | 8GB+ | 性价比之选 | 默认模型 |
| `gemma3:4b` | 4GB+ | 轻量/CPU | 速度快，适合简单任务 |
| `gemma4:e2b` | 8GB+ | Gate 准确率高 | 分类 100%，但推理较慢 |
| `deepseek-r1:8b` | 8GB+ | 推理型任务 | 需用 chat API，不传 stop 参数 |
| `deepseek-v3` | API | DeepSeek API 用户 | 通过 deepseekapi 专家使用 |

> 通过 Ollama 管理模型：`ollama pull <模型名>` 下载，`ollama list` 查看已有模型。

## 预置专家

| 专家 | 触发关键词 | 说明 |
|------|-----------|------|
| APIExpert | api, 接口, endpoint | REST API 设计与生成 |
| BugfixExpert | bug, 修复, 报错 | Bug 定位与修复 |
| RefactorExpert | 重构, 优化, 拆分 | 代码重构 |
| TestgenExpert | 测试, test, 单元测试 | 自动生成测试用例 |
| DocstringExpert | 注释, 文档, docstring | 代码文档生成 |
| TypehintExpert | 类型, type hint | 类型标注补全 |
| FastAPIExpert | fastapi, 路由 | FastAPI 项目专用 |
| SpringBootExpert | spring, springboot | Spring Boot 项目专用 |
| MyBatisExpert | mybatis, mapper | MyBatis 映射与 SQL |
| SQLOptExpert | sql, 慢查询, 索引 | SQL 优化 |
| UniAppExpert | uniapp, 小程序 | UniApp 跨端开发 |
| DeepSeekAPIExpert | deepseek, api调用 | DeepSeek API 集成 |

## 配置

KwQode 通过命令行参数和环境变量配置，无需配置文件。

**环境变量：**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OLLAMA_HOST` | Ollama 服务地址 | `http://localhost:11434` |
| `OLLAMA_MODELS` | 模型存储路径/镜像 | 系统默认 |
| `KAIWU_MODEL` | 默认模型 | `qwen3-8b` |

**项目级配置：**

每个项目根目录的 `KAIWU.md` 文件记录项目架构、技术栈和偏好，KwQode 会自动读取并作为上下文传给 LLM。

```bash
kwqode init    # 自动扫描项目结构，生成 KAIWU.md
```

## 常见问题

**Q: Ollama 连接失败？**

确认 Ollama 正在运行：
```bash
ollama serve          # 启动 Ollama 服务
ollama list           # 确认有可用模型
curl localhost:11434  # 测试连接
```

**Q: 模型下载太慢？**

国内用户可设置镜像加速：
```bash
# pip 使用清华镜像
pip install kaiwu -i https://pypi.tuna.tsinghua.edu.cn/simple

# Ollama 模型可从 ModelScope 下载后手动导入
```

**Q: 模型推理太慢？**

- 确认 GPU 被正确识别：`nvidia-smi` 或 `kwqode status`
- 换用更小的模型：`kwqode -m gemma3:4b "你的任务"`
- Reasoning 模型（如 deepseek-r1）可关闭 thinking：自动优化已内置

**Q: Windows 下中文乱码？**

KwQode 已内置 GBK 编码修复。如仍有问题：
```powershell
# PowerShell 设置 UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001
```

**Q: 如何接入 Claude Code / Cursor？**

通过 MCP 协议：
```bash
kwqode serve-mcp -m qwen3:8b
```
在 IDE 的 MCP 配置中添加 KwQode 作为 stdio server 即可。

**Q: 如何创建自定义专家？**

```bash
kwqode expert create my-expert    # 生成模板 YAML
# 编辑 ~/.kaiwu/experts/my-expert.yaml
# 配置 trigger_keywords、system_prompt、pipeline
kwqode expert list                # 确认已加载
```

## 系统要求

- Python >= 3.10
- Ollama（推荐）或 llama.cpp
- 4GB+ 显存（推荐 8GB+）
- Windows 10+、macOS 12+、Ubuntu 20.04+

## 贡献

欢迎提交 Issue 和 PR。

```bash
# 开发环境
git clone https://github.com/kaiwu-agent/kaiwu.git
cd kaiwu
pip install -e ".[dev]"
python -m pytest kaiwu/tests/
```

## License

MIT
