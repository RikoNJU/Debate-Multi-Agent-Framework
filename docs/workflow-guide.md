# Debate 论文评审项目工作流程说明

本文档以端到端的视角说明本项目的运行工作流：从输入论文开始，经过多视角独立初审、定向 Debate、证据检索、综合裁决，到最后复用原 Step 6/7 输出兼容的评审结果。


```

## 2. 入口与启动方式

本项目提供两条执行路径：

| 入口 | 说明 |
|---|---|
| `debate-demo --input xxx.json --output yyy.json` | 离线演示，读取论文输入并输出 JSON 结果（`cli.py`） |
| `python -m debate_agent_framework.main` | 启动 FastAPI 服务（`main.py`） |

### 2.1 离线 CLI（`backend/.../cli.py`）

1. 解析 `--input` 指定的 JSON 文件为 `DebateReviewInput`；
2. 用 `DebateWorkflowServices` 装配全部 Demo Agent；
3. 调用 `workflow.run(...)` 同步执行完整链路；
4. 将 `DebateRunResult` 序列化后写入 `--output` 或打印到标准输出。

### 2.2 Web API（`routers/runs.py`）

- `POST /runs`：创建任务，返回 `task_id`（202 Accepted），后台异步执行；
- `GET /runs/{task_id}`：查询任务状态与结果。

任务生命周期由 `services/workflow_service.py` + `services/jobs.py`（内存存储）管理：`pending → running → succeeded / failed`。

## 3. LangGraph 状态机编排

核心编排位于 `workflows/debate.py` 的 `DebateWorkflow._build_graph()`，是一个串行图：

```text
START
  → step1_classify_paper    自动识别论文类型（显式输入时跳过）
  → step2_classify_chapters 按论文类型识别章节阶段
  → retrieve_historical_advice 检索旧 Step 3 历史建议
  → build_context           Context Planner 构造评审上下文
  → independent_review      三个 Specialist 并行独立初审
  → plan_debate             Chair 识别争议并制定 Debate 计划
  → retrieve_debate_evidence 按需检索外部证据
  → targeted_debate         相关 Specialist 定向回应
  → synthesize_review       Chair 形成全文裁决与 Step 4 兼容输出
  → step5_workload_evaluation 按三类旧标准评价结构与工作量
  → compatibility_gate      校验章节键与章节名一致性
  → step6_summary_advice    复用原 Step 6 汇总修改建议
  → retrieve_score_cases    检索历史评分案例
  → step7_scoring           复用原 Step 7 综合评分
  → END
```

共享状态定义在 `workflows/state.py` 的 `DebateState`（TypedDict），跨节点传递上下文、独立意见、Debate 计划、证据、回应、综合裁决、评分和过程 `issues`。

运行时配置 `DebateWorkflowConfig` 暴露：最大并发数、最低独立初审数、证据条数上限、历史案例条数上限。

## 4. 各节点工作内容

### 4.1 build_context（Context Planner）

根据论文长度决定上下文形态（`agents/context_planner.py`）：

- 短文：直接使用全文；
- 长文：按相邻章节生成语义完整的内容包（`ContentPacket`），并记录包间依赖；
- 同时构造 `PaperProfile`（研究问题、贡献、全局摘要、章节关系）。

`ReviewContext` 必须包含全文或至少一个内容包，且 `paper_id` 必须与输入一致。

### 4.2 independent_review（三视角独立初审）

三个 `DemoSpecialist` 并行（受 `max_concurrency` 限制）独立评审：

| 角色 | 关注点 | 演示聚焦章节 |
|---|---|---|
| `scientific_soundness` | 理论与方法、推导、假设边界 | 方法 / 模型 / 系统设计 |
| `empirical_evidence` | 实验、Baseline、消融、可复现性 | 实验 / 评估 / 结果 |
| `global_quality` | 结构、章节关系、工作量、表达 | 引言 / 绪论 / 结论 |

特点：第一轮不读取其他 Agent 意见；任一视角失败只产生 WARNING，不中断整条链路；独立意见数量低于 `minimum_independent_reviews` 才整体报错。

每个 Specialist 输出 `IndependentReview`（摘要、优点、`ReviewFinding`、作者问题、置信度）。

### 4.3 plan_debate（Chair 识别争议）

`ReviewChair.plan_debate` 从独立意见中提取冲突：

- 找到存在分歧的争议（`DebateIssue`，含参与角色、冲突 finding、证据缺口、优先级）；
- 为每个参与角色生成定向质疑（`DebateQuestion`）；
- 需外部事实支持的问题标记 `requires_external_evidence` 并附 `evidence_query`。

V0 固定一轮 Debate；没有争议时 `DebatePlan` 为空，后续直接进入综合。

### 4.4 retrieve_debate_evidence（按需证据检索）

仅对计划中 `requires_external_evidence=True` 且带查询的问题去重后执行 `EvidenceRetriever.retrieve`，为定向回应提供外部证据。检索失败或未配置 Retriever 时降级为空证据并记录 issue，不中断流程。

### 4.5 targeted_debate（定向回应）

Chair 把每个问题派发给对应的目标 Specialist。被质询的 Specialist 阅读：自身初审、争议定义、对方（peer）意见和外部证据，然后给出立场（maintain / revise / concede / insufficient）与回应。回应与问题的角色、question_id、issue_id 必须一一对应；失败仅记 issue。

### 4.6 synthesize_review（Chair 综合裁决）

`ReviewChair.synthesize` 基于证据而非多数投票形成最终结论：

- 每个 finding 绑定证据后转为 `ResolvedFinding`，标记 `ResolutionStatus`；
- 生成 `GlobalReview`（全文维度、优点缺点、作者问题、未决争议）；
- 生成 `chapter_evaluation`（`chapter_N → CompatibleChapterEnvelope`）；Step 5 在下一独立节点覆盖工作量占位结果。

### 4.7 step5_workload_evaluation（原 Step 5 适配）

理论研究、方法创新和工程实现分别使用旧项目标准。摘要、目录、正文、参考文献、
章节字数和致谢等客观事实由确定性代码计算；真实模型结合论文类型、章节阶段和
Agent 已确认问题撰写整体工作量分析。MinerU 解析置信度只触发人工核对，不作为扣分项。

### 4.8 compatibility_gate（兼容性校验）

调用原 Step 6/7 前强制校验：

- `chapter_evaluation` 的键必须严格为 `chapter_1..chapter_N`（与可评审章节一一对应）；
- 每个键对应的章节名必须与输入一致；
- 校验失败抛出 `WorkflowExecutionError`，防止把不兼容的结构喂给原流程。

### 4.9 step6_summary_advice（原 Step 6 适配）

`OriginalPipelineAdapter.summarize_advice` 最多选择五条关键建议，并在存在多个问题章节时保持跨章节覆盖。每条建议绑定严重程度、finding、evidence、章节与人工复核标记；未知 ID 会被丢弃，争议未决结论不会被写成确定要求。执行失败会使任务失败。

### 4.10 retrieve_score_cases（历史评分 RAG）

在事实评审完成之后，用 `ScoreCalibrationQuery`（论文类型、维度摘要、严重 finding）检索历史评分案例，仅用于评分尺度校准，不修改已形成的评审事实。失败则跳过。

### 4.11 step7_scoring（原 Step 7 适配）

`OriginalPipelineAdapter.score` 输出 `ComprehensiveScoreResult`：保留原十二项语义评分（`scores["1"]..["12"]`）、总分、等级、综合评语、校准说明和置信度。失败时只记 ERROR issue，其余结果仍可返回。

## 5. 数据契约与兼容性

所有跨 Agent 数据都通过 `schemas/domain.py` 的 Pydantic 模型（`StrictModel`，拒绝未知字段）约束：

- 输入：`DebateReviewInput` 承接原 Step 1/2/3 结果；
- 上下文：`ReviewContext` → 三个 Specialist 共享；
- 输出：`ReviewSynthesis`（Step 4/5 兼容）、`SummaryAdviceResult`（Step 6）、`ComprehensiveScoreResult`（Step 7）；
- 过程记录：`DebateWorkflowIssue` 汇总各节点降级与警告。

## 6. 可替换边界（ports）

`ports/interfaces.py` 用 Protocol 定义全部可替换能力，生产环境无需重写工作流：

| 接口 | 用途 | 当前实现 |
|---|---|---|
| `ContextPlanner` | 构造评审上下文 | `DemoContextPlanner` |
| `SpecialistAgent` | 独立初审 + 定向回应 | `DemoSpecialist` |
| `ReviewChair` | 争议路由 + 综合裁决 | `DemoReviewChair` |
| `EvidenceRetriever` | 按需外部证据 | `DemoEvidenceRetriever` |
| `HistoricalScoreRetriever` | 历史评分校准 | `DemoHistoricalScoreRetriever` |
| `OriginalPipelineAdapter` | 包装原 Step 6/7 | `DemoOriginalPipelineAdapter` |

替换真实 LLM、真实 RAG 或原项目 Step 6/7 时，只需在 `DebateWorkflowServices` 里换掉对应实现。

## 7. 关键设计原则

1. **多视角而非多数投票**：最终裁决基于证据与争议回应，不是简单统计；
2. **全文视角**：每个 Specialist 都基于完整论文上下文，只是关注点不同；
3. **只争关键争议**：Chair 只对真正的冲突组织定向 Debate，控制成本；
4. **按需证据**：Evidence RAG 只在需要外部事实时触发；
5. **兼容优先**：输入/输出结构对齐原 Step 1-7，避免重构原系统。
