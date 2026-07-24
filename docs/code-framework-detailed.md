# Debate 论文评审 Multi-Agent 代码框架详细说明

## 1. 框架定位

本项目是论文评审任务的 Evidence-Grounded Debate Multi-Agent 后端框架。它面向原睿文智评流程中的核心评审阶段，在保持原 Step 4/5 输出兼容的前提下，引入多个专业 Agent 的独立判断、定向争议讨论和最终裁决。

核心目标是：

```text
复用原流程 → 引入多视角评审 → 对关键争议进行 Debate → 输出兼容原 Step 4/5/6/7 的结果
```

当前实现使用确定性 Demo Agent 跑通闭环。真实业务中，可以把 Demo Specialist、Review Chair、Evidence RAG 和 Original Pipeline Adapter 替换为真实模型和原系统函数。

## 2. 根目录设置逻辑

| 目录或文件 | 存放内容 | 作用 |
|---|---|---|
| `backend/` | 后端工程主体 | 存放正式源码、配置、Prompt 和后端说明 |
| `backend/src/debate_agent_framework/` | Python 包源码 | Debate Multi-Agent 框架核心代码 |
| `frontend/` | 前端预留目录 | 后续扩展 Web 界面时使用 |
| `docs/` | 设计文档和代码说明 | 用于汇报、交接和开发参考 |
| `examples/` | 示例输入 | 用于 CLI demo、测试和讲解 |
| `tests/` | 自动化测试 | 验证工作流、API 和原流程兼容性 |
| `assets/` | 图片资源 | 存放流程图等静态资源 |
| `output/` | 运行输出目录 | 本地 demo 输出结果，默认不提交 |
| `README.md` | 项目首页说明 | 面向仓库访问者的快速介绍 |
| `pyproject.toml` | Python 项目配置 | 定义依赖、包路径、测试配置和命令行入口 |

## 3. 后端包目录设置逻辑

核心源码位于：

```text
backend/src/debate_agent_framework/
```

| 目录 | 存放文件 | 作用 |
|---|---|---|
| `agents/` | `demo.py` | 存放 Demo Specialist、Review Chair、RAG 和原流程适配器 |
| `adapters/` | `workflow_factory.py` | 装配 Agent、RAG、原流程适配器和 Workflow |
| `config/` | `settings.py`、`settings.example.json` | 管理 API 前缀、端口、CORS 等配置 |
| `core/` | `errors.py` | 存放核心异常和基础公共能力 |
| `data/` | `.gitignore` | 后端运行数据目录占位 |
| `models/` | `schemas.py`、`api.py` | 定义评审输入、争议、证据、综合输出等数据结构 |
| `ports/` | `interfaces.py` | 定义可替换 Agent、RAG 和原流程接口 |
| `prompts/` | `context/`、`specialists/`、`chair/` | 存放未来真实 Agent 的 Prompt 模板 |
| `routers/` | `health.py`、`runs.py` | FastAPI 路由入口 |
| `services/` | `workflow_service.py`、`jobs.py` | 管理任务生命周期和运行状态 |
| `web/` | `__init__.py` | Web 相关兼容出口 |
| `workflows/` | `debate.py`、`state.py` | 定义 LangGraph 工作流和共享状态 |

### 3.1 目录内容说明

`agents/` 存放具体 Agent 或工具的实现。当前包含 Demo Context Planner、Demo Specialist、Demo Review Chair、Demo Evidence Retriever、Demo Historical Score Retriever 和 Demo Original Pipeline Adapter。未来接入真实模型时，专业评审 Agent、裁决 Agent、证据检索实现和原流程包装实现都可以放在这里。

`adapters/` 存放系统装配和外部能力接入代码。当前通过 `workflow_factory.py` 把 Demo Agent、RAG 和原流程适配器组装成 Debate 工作流。未来替换真实 LLM、Evidence RAG、历史评分 RAG 或睿文智评原 Step 6/7 函数时，应优先修改这里。

`config/` 存放后端运行配置。当前包括环境变量读取逻辑和示例配置。端口、API 前缀、CORS、模型服务地址、RAG 服务地址、原系统接口地址等运行参数都应放在这里。

`core/` 存放框架级公共能力。当前只包含工作流异常类型。未来如果出现跨目录复用的基础错误、日志上下文、运行标识或通用常量，可以放在这里，但不应放具体评审逻辑。

`data/` 是后端运行数据占位目录。它适合存放本地缓存、临时索引、调试数据或小规模 demo 数据。生产环境中的数据库文件、向量索引和运行输出一般不应直接提交到 Git。

`models/` 存放数据结构和数据校验规则。评审输入、章节信息、独立评审、争议计划、证据、回应、综合评审、Step 4/5 兼容输出和 Step 7 评分结果都在这里定义。该目录决定系统输出是否稳定、是否能继续被原流程消费。

`ports/` 存放能力接口，而不是能力实现。它规定系统需要哪些能力，例如 Context Planner、Specialist Agent、Review Chair、Evidence Retriever、Historical Score Retriever 和 Original Pipeline Adapter。Workflow 只依赖这些接口，因此可以替换具体实现。

`prompts/` 存放 Prompt 模板和 Prompt 管理说明。真实 Context Planner、三个 Specialist 和 Review Chair 的提示词都应集中放在这里，便于版本管理、评审和迭代。不要把长 Prompt 直接写进路由或工作流节点。

`routers/` 存放 API 路由。它只负责接收请求、调用 Service、返回响应。该目录不应该直接写 Debate 调度逻辑，也不应该直接操作 Agent、RAG 或任务状态细节。

`services/` 存放应用服务和任务状态管理。它连接 API 层和 Workflow 层，负责创建任务、执行任务、记录成功或失败结果。当前使用内存任务存储，后续可替换为 Redis、数据库或任务队列。

`web/` 当前只作为 Web 相关兼容出口，帮助旧导入路径过渡。随着结构稳定，新的 Web 任务状态逻辑应优先放到 `services/`，不要继续扩大 `web/` 的职责。

`workflows/` 存放 Multi-Agent 协作流程。这里定义 LangGraph 节点、节点之间的边、并行初审、争议路由、证据检索、定向回应、兼容性校验和 Step 6/7 调用。该目录是“Agent 如何协作”的核心，但不直接实现具体模型能力。

## 4. Python 文件功能、输入输出与系统作用

### 4.1 包入口

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `__init__.py` | 提供包级轻量出口 | 无直接输入 | 导出 `DebateReviewInput` | 让外部快速引用基础输入模型，避免导入包时强制加载完整工作流 |
| `main.py` | 创建 FastAPI 应用 | `DebateWebSettings` 或环境变量 | `FastAPI` 实例 | API 服务入口，挂载健康检查和任务接口 |
| `cli.py` | 命令行 demo 入口 | 示例评审 JSON 路径、输出路径 | Debate 评审结果 JSON | 用于本地验证完整评审链路 |

### 4.2 `models/`

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `models/schemas.py` | 定义 Debate 工作流核心数据模型 | 论文、章节、历史建议、证据、争议、回应、评分字段 | Pydantic 模型 | 约束 Agent 协作协议和原 Step 4/5/7 兼容输出 |
| `models/api.py` | 定义 API 健康检查响应 | 服务名、版本、状态 | `HealthResponse` | 让 API 返回稳定健康检查结构 |
| `models/__init__.py` | 聚合导出模型 | 无直接输入 | `DebateReviewInput`、`ReviewSynthesis`、`ComprehensiveScoreResult` 等 | 统一模型导入路径 |

主要数据流：

```text
DebateReviewInput
→ ReviewContext
→ IndependentReview
→ DebatePlan
→ ReviewEvidence
→ DebateResponse
→ ReviewSynthesis
→ SummaryAdviceResult
→ ComprehensiveScoreResult
→ DebateRunResult
```

关键兼容模型：

| 模型 | 对应原流程 | 作用 |
|---|---|---|
| `CompatibleChapterData` | 原 Step 4 | 保存章节评审结果 |
| `CompatibleChapterEnvelope` | 原 Step 4 | 保留 `chapter_data` 包装结构 |
| `CompatibleWorkloadEvaluation` | 原 Step 5 | 保存结构规范性、摘要关键词、目录、章节和致谢评价 |
| `SummaryAdviceResult` | 原 Step 6 | 保存修改建议汇总 |
| `ComprehensiveScoreResult` | 原 Step 7 | 保存 12 项评分、总分、等级和总评 |

### 4.3 `ports/`

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `ports/interfaces.py` | 定义可替换能力接口 | 评审输入、上下文、争议、证据、历史案例 | 上下文、独立评审、争议计划、综合结果、评分结果 | 把工作流和具体模型、RAG、原系统函数解耦 |
| `ports/__init__.py` | 聚合导出接口 | 无直接输入 | `ContextPlanner`、`SpecialistAgent`、`ReviewChair` 等 | 给实现方提供统一接口引用 |

关键接口：

| 接口 | 输入 | 输出 | 作用 |
|---|---|---|---|
| `ContextPlanner.build` | `DebateReviewInput` | `ReviewContext` | 构造全文或分包上下文 |
| `SpecialistAgent.review` | `ReviewContext` | `IndependentReview` | 专业 Agent 独立初审 |
| `SpecialistAgent.respond` | 上下文、自身初审、争议、问题、同伴意见、外部证据 | `DebateResponse` | 对 Chair 定向问题作出回应 |
| `ReviewChair.plan_debate` | 上下文、独立评审列表 | `DebatePlan` | 识别冲突和证据缺口 |
| `ReviewChair.synthesize` | 上下文、初审、争议计划、回应、外部证据 | `ReviewSynthesis` | 形成最终裁决和 Step 4/5 兼容输出 |
| `EvidenceRetriever.retrieve` | 检索 query、上下文、limit | `ReviewEvidence` 列表 | 为争议问题补充外部证据 |
| `HistoricalScoreRetriever.retrieve` | 评分校准 query、limit | `HistoricalScoreCase` 列表 | 为 Step 7 提供历史评分尺度参考 |
| `OriginalPipelineAdapter.summarize_advice` | 评审输入、综合评审 | `SummaryAdviceResult` | 包装或复用原 Step 6 |
| `OriginalPipelineAdapter.score` | 评审输入、综合评审、建议汇总、历史案例 | `ComprehensiveScoreResult` | 包装或复用原 Step 7 |

### 4.4 `agents/`

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `agents/demo.py` | 提供确定性 Demo 实现 | `DebateReviewInput`、`ReviewContext`、`DebatePlan`、证据和评分 query | 上下文、独立评审、争议计划、回应、综合结果、评分 | 在没有真实模型和原系统时跑通完整链路 |
| `agents/__init__.py` | 聚合导出 Demo 实现 | 无直接输入 | Demo Agent、RAG、Adapter 类 | 统一 Agent 导入路径 |

Demo 类职责：

| 类 | 输入 | 输出 | 作用 |
|---|---|---|---|
| `DemoContextPlanner` | `DebateReviewInput` | `ReviewContext` | 构造全文上下文或内容包 |
| `DemoSpecialist` | `ReviewContext`、争议问题 | `IndependentReview`、`DebateResponse` | 模拟三个专业评审 Agent |
| `DemoReviewChair` | 独立评审、回应、证据 | `DebatePlan`、`ReviewSynthesis` | 模拟争议识别和最终裁决 |
| `DemoEvidenceRetriever` | 外部证据 query | `ReviewEvidence` | 模拟 Evidence RAG |
| `DemoHistoricalScoreRetriever` | 评分校准 query | `HistoricalScoreCase` | 模拟历史评分 RAG |
| `DemoOriginalPipelineAdapter` | 综合评审、历史案例 | `SummaryAdviceResult`、`ComprehensiveScoreResult` | 模拟原 Step 6/7 |

### 4.5 `workflows/`

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `workflows/state.py` | 定义共享状态、配置和依赖容器 | Agent、RAG、原流程适配器、工作流参数 | `DebateState`、`DebateWorkflowConfig`、`DebateWorkflowServices` | 规定各节点共享哪些字段、注入哪些能力 |
| `workflows/debate.py` | 定义 LangGraph Debate 流程 | `DebateReviewInput` 或 dict | `DebateRunResult` | 系统核心编排层，控制初审、争议识别、证据检索、定向回应、综合裁决和 Step 6/7 |
| `workflows/__init__.py` | 聚合导出工作流 | 无直接输入 | `DebateWorkflow` 等 | 统一工作流导入路径 |

`DebateWorkflow` 的节点逻辑：

```text
build_context
→ independent_review
→ plan_debate
→ retrieve_debate_evidence
→ targeted_debate
→ synthesize_review
→ compatibility_gate
→ step6_summary_advice
→ retrieve_score_cases
→ step7_scoring
```

重要输入输出：

| 方法 | 输入 | 输出 | 作用 |
|---|---|---|---|
| `arun` | `DebateReviewInput` 或 dict | `DebateRunResult` | 异步执行完整 Debate 工作流 |
| `run` | `DebateReviewInput` 或 dict | `DebateRunResult` | 同步执行完整 Debate 工作流 |
| `_build_context` | `DebateState` | `ReviewContext` | 构造评审上下文 |
| `_independent_review` | `ReviewContext` | 多个 `IndependentReview` 和 issues | 并行调用三个 Specialist 初审 |
| `_plan_debate` | 初审结果 | `DebatePlan` | 由 Chair 判断是否需要 Debate |
| `_retrieve_debate_evidence` | Debate 问题中的 evidence query | 外部 `ReviewEvidence` | 为争议补充证据 |
| `_targeted_debate` | 争议计划、同伴意见、外部证据 | `DebateResponse` 列表 | 让相关 Specialist 定向回应 |
| `_synthesize_review` | 初审、回应、证据 | `ReviewSynthesis` | 形成最终评审结论和兼容输出 |
| `_compatibility_gate` | `ReviewSynthesis`、原章节列表 | 空 dict 或异常 | 检查 Step 4/5 输出是否兼容原流程 |
| `_step6_summary_advice` | 评审输入、综合评审 | `SummaryAdviceResult` | 调用原 Step 6 适配器 |
| `_retrieve_score_cases` | 综合评审严重问题和维度摘要 | `HistoricalScoreCase` 列表 | 为评分尺度校准提供案例 |
| `_step7_scoring` | 综合评审、建议汇总、历史案例 | `ComprehensiveScoreResult` | 调用原 Step 7 适配器 |

### 4.6 `adapters/`

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `adapters/workflow_factory.py` | 装配 Debate 工作流依赖 | 无直接业务输入 | `DebateWorkflow` | 决定当前使用 Demo 实现还是真实实现 |
| `adapters/__init__.py` | 导出 factory | 无直接输入 | `build_debate_workflow` | 给 service 层提供统一构造入口 |

当前 `build_debate_workflow` 装配的是 Demo Context Planner、三个 Demo Specialist、Demo Review Chair、Demo Evidence RAG、Demo Historical Score RAG 和 Demo Original Pipeline Adapter。生产环境应主要修改这里。

### 4.7 `services/`

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `services/workflow_service.py` | 管理一次评审任务的创建、执行和查询 | `DebateReviewInput`、task_id | `RunSnapshot` | 连接 API 层和 Workflow 层 |
| `services/jobs.py` | 进程内任务存储 | task_id、任务结果、错误信息 | `RunSnapshot` | 保存任务状态，支持前端轮询 |
| `services/__init__.py` | 聚合导出服务 | 无直接输入 | `DebateWorkflowService` | 统一服务导入路径 |

任务状态流转：

```text
queued → running → succeeded / failed
```

当前使用 `InMemoryRunStore`，只适合开发和 demo。生产环境应替换为 Redis、数据库或任务队列。

### 4.8 `routers/`

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `routers/health.py` | 健康检查接口 | HTTP GET 请求 | `HealthResponse` | 验证 API 服务是否可用 |
| `routers/runs.py` | Debate 评审任务接口 | `DebateReviewInput`、task_id | `RunSnapshot` | 提交任务、查询任务状态和结果 |
| `routers/__init__.py` | 聚合导出路由 | 无直接输入 | `health_router`、`runs_router` | 给 `main.py` 统一挂载路由 |

API 路径：

```text
GET  /api/debate/health
POST /api/debate/runs
GET  /api/debate/runs/{task_id}
```

### 4.9 `config/`

| 文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `config/settings.py` | 读取 Web 配置 | 环境变量 | `DebateWebSettings` | 控制应用名、host、port、API 前缀、CORS |
| `config/settings.example.json` | 示例配置 | 无 | JSON 示例 | 给部署和联调提供参考 |
| `config/__init__.py` | 聚合导出配置 | 无直接输入 | `DebateWebSettings` | 统一配置导入路径 |

### 4.10 `core/`、`web/`、`prompts/`、`data/`

| 目录或文件 | 功能 | 输入 | 输出 | 系统作用 |
|---|---|---|---|---|
| `core/errors.py` | 定义 `WorkflowExecutionError` | 错误消息 | 工作流异常 | 区分框架级失败和普通 Python 异常 |
| `web/__init__.py` | Web 兼容出口 | 无直接输入 | 任务状态相关类 | 保留旧导入兼容，后续可弱化 |
| `prompts/README.md` | Prompt 管理说明 | 无 | 文档 | 约束 Prompt 不散落在代码里 |
| `prompts/context/planner.md` | Context Planner Prompt 占位 | 论文输入 | 评审上下文 | 后续接真实上下文构造 Agent |
| `prompts/specialists/*.md` | Specialist Prompt 占位 | 评审上下文 | 独立评审和回应 | 后续接三个专业评审 Agent |
| `prompts/chair/*.md` | Review Chair Prompt 占位 | 多 Agent 意见、证据和争议 | Debate 计划、最终综合评审 | 后续接真实裁决 Agent |
| `data/.gitignore` | 数据目录占位 | 本地数据文件 | 不提交运行数据 | 为缓存、索引、临时文件预留空间 |

## 5. 系统调用关系

```text
HTTP 请求
→ routers/runs.py
→ services/workflow_service.py
→ adapters/workflow_factory.py
→ workflows/debate.py
→ agents/demo.py 或真实 Agent
→ ports/interfaces.py 约束外部能力
→ models/schemas.py 约束输入输出
```

CLI 调用关系：

```text
examples/review_input.json
→ cli.py
→ DebateWorkflow
→ DebateRunResult
→ output/result.json
```

## 6. 开发规范

1. 新业务数据结构必须先写入 `models/`，不要在 Agent 或路由里临时拼 dict。
2. 新外部能力必须先在 `ports/` 定义接口，再在 `adapters/` 或 `agents/` 中实现。
3. `workflows/` 只负责流程编排，不直接写具体模型 SDK、数据库连接或复杂 Prompt。
4. `agents/` 负责专业判断和结构化输出，不处理 HTTP 请求和任务状态。
5. `routers/` 只做请求校验、依赖注入和响应返回，不写业务流程。
6. `services/` 负责任务生命周期，不直接实现 Debate 评审逻辑。
7. `adapters/` 负责装配真实能力，是替换 Demo 实现的主要入口。
8. Prompt 放在 `prompts/`，需要版本化、可追踪，不要散落到多个 Python 文件中。
9. 所有 Agent 输出必须经过 Pydantic 模型校验，不能让自由文本直接进入后续流程。
10. 高严重度评审结论必须有证据；证据不足时要降低置信度或转人工复核。
11. 原 Step 4/5/6/7 兼容字段不能随意改名，修改前必须同步更新兼容测试。
12. 本地运行输出、缓存、索引和 `.egg-info` 不提交到 Git。
13. 中文文档和源码统一使用 UTF-8 编码。
14. 新增功能要补充最小测试，至少覆盖正常路径、冲突路径和一个失败/降级路径。
15. 生产环境密钥必须通过环境变量或安全配置注入，禁止写入仓库。

## 7. 后续扩展建议

优先替换以下位置：

```text
adapters/workflow_factory.py
```

把当前 Demo 实现替换为：

```text
真实 Context Planner
真实 Scientific Soundness Specialist
真实 Empirical Evidence Specialist
真实 Global Quality Specialist
真实 Review Chair
真实 Evidence Retriever
真实 Historical Score Retriever
真实 Original Pipeline Adapter
生产级任务存储
```

只要这些实现遵守 `ports/` 中的接口，`workflows/`、`models/` 和原流程兼容测试通常不需要重写。
