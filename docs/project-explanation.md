# 项目原理与工作流程详解

本项目是一个面向论文评审任务的 **Evidence-Grounded Debate Multi-Agent 后端框架**。本文档从设计原理和代码级工作流程两方面详细讲解。

## 一、项目要解决的问题

原"睿文智评"系统用单个模型按 Step 1→7 顺序评审论文。单模型一轮推理容易：遗漏问题、把"理论评价/实验评价/结构评价/评分"混在一起、结论缺少证据支撑。本项目在**核心评审阶段（Step 4/5）**插入多智能体协作，同时保持与原有流程的输出兼容。

## 二、核心原理

### 1. 职责分工（不是简单多次调 Prompt）

```text
Context Planner          → 构造统一全文评审上下文
Specialist ×3            → 三个全文视角独立初审
Review Chair             → 识别争议、路由问题、最终裁决
Evidence Retriever       → 为关键争议补充外部证据
Original Pipeline Adapter → 复用原 Step 6/7
```

三个 Specialist 都读**同一份全文**，只是关注点不同（`schemas/domain.py:22-25`）：

- **Scientific Soundness**：理论、方法合理性、结论一致性
- **Empirical Evidence**：实验设计、Baseline、消融、可复现性
- **Global Quality**：结构、章节联系、工作量、表达

### 2. 三个关键设计原则

- **独立初审**：第一轮每个 Agent 不读取其他人意见，避免过早趋同（`ports/interfaces.py:39`）
- **定向 Debate 而非多数投票**：Chair 只把关键争议发给相关 Agent 交叉质疑，最终结论"基于原文证据 + 回应质量"裁决，不用简单多数（`agents/review_chair.py:172`）
- **证据边界强制约束**：高严重度问题必须有原文或外部证据，无证据必须降低置信度或转人工复核（`schemas/domain.py:170-177, 278-284`），这是纯 Pydantic 校验强制执行的

### 3. 兼容原流程

- 输入 `DebateReviewInput` 承接原 Step 1/2/3 结果（分类、章节、检索建议）
- `ReviewSynthesis` 同时输出 `global_review`（全文裁决）+ `chapter_evaluation`（原 Step 4 的 `chapter_N` 结构）+ `workload_evaluation`（原 Step 5 五项结构评价）
- `OriginalPipelineAdapter` 包装原 Step 6（修改建议）/ Step 7（十二项评分）

## 三、一次运行的全景图

运行 `debate-demo --input examples/review_input.json` 时，控制流如下：

```text
cli.py:main()
  → DebateWorkflowServices(全部 Demo 实现)
  → DebateWorkflow(...).run()  → asyncio.run(arun())
  → StateGraph 按边执行 10 个节点
  → DebateRunResult.model_dump_json() 写文件
```

数据流全链路：

```text
DebateReviewInput
→ ReviewContext        (Context Planner)
→ IndependentReview×3  (三个 Specialist 并行独立初审)
→ DebatePlan           (Chair 识别争议)
→ ReviewEvidence       (Evidence RAG，按需)
→ DebateResponse       (相关 Specialist 定向回应)
→ ReviewSynthesis      (Chair 全文裁决 + Step4/5 兼容输出)
→ SummaryAdviceResult  (Step 6)
→ HistoricalScoreCase  (评分 RAG，后置)
→ ComprehensiveScoreResult (Step 7)
→ DebateRunResult
```

## 四、数据契约层 `schemas/domain.py` — 全系统的"宪法"

所有 Agent 之间传递的数据必须先在这里定义，用 Pydantic 强制校验。三个设计要点：

### 1. `StrictModel` 基类（第 10-13 行）

```python
model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
```

**`extra="forbid"` 是最关键的一条**：如果某个 Agent 偷偷多返回一个字段、或少返回一个字段，Pydantic 直接抛 `ValidationError`，工作流在节点层就捕获并报 `WorkflowExecutionError`。这防止"Agent 悄悄改变协作协议"。`ReviewSynthesis`、`DebateRunResult` 等派生模型都继承这个严格行为。

### 2. 五个核心领域对象

| 模型 | 输入/输出方 | 关键字段 |
|---|---|---|
| `ReviewContext`(117行) | ContextPlanner 输出 | `profile` + `full_text` **或** `content_packets`（互斥，`require_readable_content` 校验二者至少其一）|
| `IndependentReview`(180行) | Specialist 初审 | `role`、`findings[]`、`confidence` |
| `DebatePlan`(228行) | Chair 计划 | `issues[]` + `questions[]`，有 4 条引用完整性校验 |
| `DebateResponse`(250行) | Specialist 回应 | `position`(maintain/revise/concede/insufficient)、`revised_findings[]` |
| `ReviewSynthesis`(359行) | Chair 最终输出 | `global_review` + `chapter_evaluation` + `workload_evaluation` |

### 3. 证据约束是"硬编码校验"，不是靠模型自觉

`ReviewFinding.enforce_evidence_boundary`（170-177 行）：

```python
if self.severity in {FATAL, MAJOR} and not self.evidence:
    if self.confidence > 0.5 or not self.requires_human_review:
        raise ValueError("无证据的高严重度问题必须降低置信度并标记人工复核")
```

含义：**高严重度问题必须有证据**。如果没有证据，只有两条出路：把置信度降到 ≤0.5，或者标记 `requires_human_review=True`。二者都不满足就报错。`ResolvedFinding.enforce_final_evidence_boundary`（278-284 行）在最终裁决层重复这道闸门。测试 `test_high_severity_finding_without_evidence...` 专门验证了这一点（`tests/test_workflow.py:231`）。

`ReviewEvidence.external_source_requires_locator`（148-152 行）：外部证据必须有 DOI 或 URL，否则不可追溯，直接拒绝。

### 4. 兼容原流程的专用模型

- `CompatibleChapterData`(320行)：字段名完全对齐原 Step 4 解析器——`chapter_name/chapter_type/chapter_summary/chapter_remark/section_structure/extracted_info/evaluation_items/scoring_impact/advice`
- `CompatibleWorkloadEvaluation`(353行)：`structure_evaluation` 内含五项 `WorkloadItem`（完整度、摘要关键词、目录、章节、致谢），字段名与原 Step 5 JSON Schema 完全一致
- `ComprehensiveScoreResult`(396行)：`scores` 必须恰好是 `"1".."12"` 十二项（407-413 行校验），每项 0-100

## 五、接口层 `ports/interfaces.py` — 依赖倒置

用 `typing.Protocol` 定义 6 个抽象能力，Workflow 只依赖这些协议，不 import 任何具体 Agent：

```python
class SpecialistAgent(Protocol):
    def review(self, context) -> MaybeAwaitable[IndependentReview]: ...   # 接口签名里没有 peer_reviews，强制"独立"
    def respond(self, context, *, own_review, issue, question,
                peer_reviews, external_evidence) -> MaybeAwaitable[DebateResponse]: ...
```

注意 `review()` 签名**只接收 context**，刻意不给其他 Agent 的意见——把"第一轮必须独立"这个业务规则固化进接口签名。

`MaybeAwaitable[T]` 是个关键技巧（26 行）：每个方法**既允许同步实现、也允许异步实现**。工作流里用 `_resolve()`（`debate.py:46-51`）统一处理：`inspect.isawaitable()` 判断后 await。所以 Demo 用 `async def`、真实实现用同步 SDK 都能接入，无需改动工作流。

`SpecialistRegistry`（123 行）只是 `Mapping[SpecialistRole, SpecialistAgent]`，即"按角色注册的字典"。

## 六、工作流层 `workflows/debate.py` — 编排核心

### 1. 装配：`DebateWorkflow.default()`（57-73 行）

```python
return cls(DebateWorkflowServices(
    context_planner=DemoContextPlanner(),
    specialists={role: DemoSpecialist(role) for role in SpecialistRole},  # 三个角色各一个实例
    review_chair=DemoReviewChair(),
    evidence_retriever=DemoEvidenceRetriever(),
    historical_score_retriever=DemoHistoricalScoreRetriever(),
    original_pipeline=DemoOriginalPipelineAdapter(),
))
```

`__init__` 里有个**注册表完整性校验**（82-86 行）：`specialists` 字典的键必须与 `SpecialistRole` 枚举完全一致，缺一个或多一个都直接 `ValueError`。这是防止"少注册一个视角"的防御。

### 2. LangGraph 图的构建（89-113 行）

`StateGraph(DebateState)` 注册 10 个节点，全用 `add_edge` 线性连接（V0 没有条件分支）：

```text
build_context → independent_review → plan_debate → retrieve_debate_evidence
→ targeted_debate → synthesize_review → compatibility_gate
→ step6_summary_advice → retrieve_score_cases → step7_scoring → END
```

`DebateState` 是 `TypedDict`（`state.py:32-43`），唯一特殊的是 `issues: Annotated[list, add]`——`add` 是 reducer，表示**每次节点返回 issues 都追加而不是覆盖**，这样错误信息一路累积到最终结果。

### 3. 配置 `DebateWorkflowConfig`（`state.py:46-63`）

```python
max_concurrency=3                # Specialist 并发上限，校验必须在 1..3
minimum_independent_reviews=2    # 至少 2 份初审才能继续
evidence_limit=8                 # 外部证据条数上限
historical_case_limit=5          # 历史评分案例上限
```

注释明确写了"V0 固定一轮 Debate"，只暴露成本与降级相关参数。

### 4. 逐节点详解（理解全流程的关键）

**节点1 `_build_context`（115-123行）**

- 调 `context_planner.build(state["review_input"])`
- `ReviewContext.model_validate(await _resolve(value))` —— 先解析 awaitable，再过 Pydantic 校验
- **额外校验**：`context.paper_id != input.paper_id` 直接报错，防止 ContextPlanner 张冠李戴

**节点2 `_independent_review`（125-157行）**

- `asyncio.Semaphore(config.max_concurrency)` 限制并发
- `asyncio.gather` 同时跑 3 个 Specialist 的 `review()`
- 每个失败都捕获成 `DebateWorkflowIssue`（code=`specialist_review_failed`），**不中断其他视角**
- 但若成功数 < `minimum_independent_reviews`(2)，整体抛 `WorkflowExecutionError`
- 角色一致性校验：`review.role is not role` 就报错——防止 A 角色的 Agent 返回了 B 角色的评审

**节点3 `_plan_debate`（159-167行）**

- Chair 根据初审产出 `DebatePlan`，`DebatePlan.model_validate` 会触发 `validate_references`（domain.py 234-247）：issue_id 无重复、question 引用的 issue 必须存在、question 的 target_role 必须参与该争议。**Chair 不能乱点将。**

**节点4 `_retrieve_debate_evidence`（169-211行）**

- 关键逻辑：从 plan 里筛出 `requires_external_evidence and evidence_query` 的 question，取 `evidence_query` 并**去重**（`dict.fromkeys` 保序）
- 没有查询 → 直接返回空，**不调用 RAG**（这是测试 `test_evidence_rag_is_not_called_without_external_question` 验证的行为）
- 检索后过滤 `kind.value == "external"`，截断到 `evidence_limit`
- 未配置 retriever 或检索失败 → 空列表 + warning issue，不阻断流程

**节点5 `_targeted_debate`（213-274行）**

- 只对有 question 的争议进行，**没有 question 就直接返回空回应**（216行）
- 为每个 question 找目标 Specialist 的 own_review、从 issue 的 `participating_roles` 找对方意见（peer_reviews）
- 同样并发 + 单点容错；`DebateResponse` 校验三重一致性：role、question_id、issue_id 必须与 question 完全匹配

**节点6 `_synthesize_review`（276-288行）**

- Chair 拿到全部素材（初审+计划+回应+外部证据）产出 `ReviewSynthesis`
- 校验失败抛错——这是全流程质量闸门

**节点7 `_compatibility_gate`（290-309行）——最严格的防线**

```python
expected_keys = [f"chapter_{index}" for index in range(1, len(reviewable)+1)]
actual_keys = list(state["synthesis"].chapter_evaluation)
if actual_keys != expected_keys:
    raise WorkflowExecutionError("chapter_evaluation 键与原流程不兼容...")
# 再逐个比对 chapter_name 必须与输入章节名一致
```

注意它基于 `reviewable=True` 的章节数生成期望键，且用 `zip(strict=True)` 严格对齐。`test_compatibility_gate_rejects_missing_step4_chapter` 验证了删掉一个章节键会立刻报错。

**节点8 `_step6_summary_advice`（311-333行）**

- 调 `original_pipeline.summarize_advice`，失败时**有降级 fallback**：返回"Step 6 执行失败"的占位 `SummaryAdviceResult` + error issue，不中断

**节点9 `_retrieve_score_cases`（335-368行）**

- 用 synthesis 里的 `global_review` 构造 `ScoreCalibrationQuery`：
  - `paper_type` 来自输入
  - `dimensions` = 各维度摘要
  - `severe_findings` = 只取 `fatal/major` 的已确认问题
- 结果按 `similarity` 降序截断——注意**评分 RAG 发生在事实评审之后**，这是"后置校准"原则的代码体现

**节点10 `_step7_scoring`（370-390行）**

- `score(input, synthesis, summary_advice, historical_cases)` —— 历史案例只作为校准参照传入
- 失败返回 error issue，但此时 workflow 仍能正常结束（只是 `final_score` 为 None）

### 5. `arun()`（392-423行）

入口先 `DebateReviewInput.model_validate` 强校验输入，再构造初始 state，`graph.ainvoke` 执行，最后检查 `context/debate_plan/synthesis` 三个必备字段必须存在，组装 `DebateRunResult`。

## 七、Demo Agent 层 `agents/demo.py` — 用规则模拟智能

没有真实模型，但完整模拟了每个角色的行为逻辑：

### DemoContextPlanner（43-89行）

- `full_text_limit=20000` 字符阈值
- 短文 → `full_text` 直接用全文，`content_packets` 为空
- 长文 → 按 **每 2 个相邻章节** 打包成 `ContentPacket`，并设置 `dependency_packet_ids`（前一包的依赖），体现"相邻章节方法/实验/结论强关联，不能机械按章节边界切分"的设计约束
- `PaperProfile` 从输入构造：research_problem=abstract、chapter_relationships=相邻章节的 `A → B` 链

### DemoSpecialist（92-207行）

- `_focus_chapter`（181-194行）：按角色偏好选章节——科学性看"方法/模型/系统设计"，实验性看"实验/评估/结果"，全局性看"引言/绪论/结论"，用 `stage` 和 `chapter_name` 模糊匹配
- 三个角色各产出一条固定 finding，且**故意设计成冲突**，制造 Debate 素材：
  - 科学性：MODERATE"方法自洽但失效边界说明不足"
  - 实验性：MAJOR"缺少强 Baseline"，`needs_external_verification=True` + `verification_query`（正是它触发节点4的检索）
  - 全局性：MINOR"回指不够清晰"
- `respond()`（152-179行）：科学性角色 `REVISE`（承认理论自洽≠贡献成立），实验性角色 `MAINTAIN`（坚持实验不足），正是 `docs/design-v0.md` §5.4 示例的重现

### DemoReviewChair（210-418行）

- `plan_debate`：**只有当 science 和 empirical 两份初审都存在**时才生成争议（222-223行），否则返回空 `DebatePlan`。生成 1 个 issue + 2 个定向 question，其中一个带 `requires_external_evidence=True`
- `synthesize`：这是最复杂的一段（260-382行），展示"证据综合"而非多数投票：
  - 每个 finding 升级为 `ResolvedFinding`，状态规则：`needs_external_verification` 的问题**有外部证据→CONFIRMED，没有→INSUFFICIENT**（282-286行）
  - 置信度微调：有 Debate 回应则 `+0.05`，上限 0.9
  - 反向观点保留：position=REVISE 的回应进 `dissenting_views`——设计文档 §7"应保留相反观点而不是强行消除分歧"
  - `chapter_evaluation` 按 `reviewable` 章节逐章组装，相关 finding 的 claim 进 `chapter_remark`、`evaluation_items` 和 `advice`
  - `_chapter_type`（384-403行）是个 15 项 stage→类型映射表，对接原系统章节类型
  - `_scoring_impact` 取最严重 finding 生成评分影响描述

### Demo 双 RAG 和原流程适配（421-502行）

- `DemoEvidenceRetriever`：返回一条带 URL 的固定外部证据
- `DemoHistoricalScoreRetriever`：返回一条相似度 0.81 的历史案例
- `DemoOriginalPipelineAdapter.score`：生成 12 项分数（`84 - (index % 5)`），并强制覆盖 `scores["6"]=76`、`scores["9"]=78` 模拟实验维度扣分，`calibration_notes` 注明"仅用于尺度校准"

## 八、真实 Agent 骨架层（未接入模型的占位）

`agents/review_chair.py` 是**唯一真正写了模型调用逻辑的骨架**，`DebateReviewChairAgent`：

- `plan_debate`/`synthesize` 把 context、初审、计划、回应、证据全部 `model_dump(mode="json")` 序列化，拼进 user prompt
- 用 `json.dumps(payload, ensure_ascii=False)` 保证中文
- `_complete_json` 调 `model_client.complete`，要求 `response_format={"type":"json_object"}`，再用 `json.loads` 解析，非 dict 顶层报错
- 产出先过 `DebatePlan`/`ReviewSynthesis` 校验再返回——**让真实模型的输出也遵守和 Demo 一样的数据契约**

`specialists.py` 和 `context_planner.py` 的 `review/build` 直接 `raise NotImplementedError`，只是占位。

## 九、模型调用层 `backend/env/model_client.py`

- 环境变量优先级：`DEBATE_*` > `LLM_*` > 默认值（`from_env` 的 `read()` 函数，53-57行）
- `ModelRuntimeConfig` 默认值：gpt-4.1-mini、temp 0.2、超时 60s
- `OpenAICompatibleChatClient` 用标准库 `urllib.request` 发请求（不依赖 requests），解析 `choices[0].message.content`
- `acomplete` 用 `asyncio.to_thread` 包同步调用——所以异步工作流里同步模型也能用
- 有个 `ModelClientError` 专门区分模型错误

## 十、服务层 + API 层

- `services/jobs.py`：`InMemoryRunStore` 用 `threading.RLock` + `dict` 存任务，`RunSnapshot` 四个状态 `queued→running→succeeded/failed`。注释明示生产要换 Redis/数据库
- `services/workflow_service.py`：`execute()` 里 `mark_running` → `workflow.arun()` → `mark_succeeded(model_dump)`；异常走 `mark_failed`。`get_debate_workflow_service` 用 `lru_cache` 单例
- `routers/runs.py`：`POST /runs` 用 FastAPI `BackgroundTasks` 把 execute 丢后台（立即返回 202 + task_id），前端轮询 `GET /runs/{task_id}`
- `main.py`：CORS 白名单来自 settings

API 路径：

```text
GET  /api/debate/health
POST /api/debate/runs
GET  /api/debate/runs/{task_id}
```

## 十一、测试如何锁住行为（4 个测试文件）

`tests/test_workflow.py` 是最核心的，8 个用例对应 8 条不可违背的规则：

| 测试 | 验证的原则 |
|---|---|
| `test_demo_runs_full_original_pipeline_compatible_flow` | 完整闭环 + Step4/5/7 兼容字段 |
| `test_reviews_are_parallel_and_debate_is_targeted` | 初审并发=3；global_quality 收到 0 次追问（定向只问相关 Agent）|
| `test_evidence_rag_is_not_called_without_external_question` | 无争议不检索证据 |
| `test_evidence_rag_is_called_once_for_deduplicated_queries` | 相同 evidence_query 只检索一次 |
| `test_one_specialist_failure_preserves_two_other_reviews` | 单视角失败降级不阻断 |
| `test_compatibility_gate_rejects_missing_step4_chapter` | 缺章节键 → 工作流报错 |
| `test_high_severity_finding_without_evidence...` | 证据边界校验 |
| `test_original_pipeline_adapter_receives_exact_step4_step5_shapes` | 适配器拿到的是原流程精确结构 |

## 十二、一张表总结"每条设计原则 → 代码落实点"

| 设计文档约束 | 代码位置 | 机制 |
|---|---|---|
| 第一轮独立，不能读他人意见 | `interfaces.py:39` | `review()` 签名只收 context |
| 一轮定向 Debate，不重跑全评审 | `debate.py:213-274` | 只对 question 的目标角色调 `respond` |
| 证据 RAG 按需调用，非默认覆盖 | `debate.py:169-188` + 测试 | 仅 `requires_external_evidence` 问题触发 |
| 高严重度必须有证据 | `domain.py:170-177, 278-284` | Pydantic 硬校验 |
| 不用多数投票裁决 | `demo.py:260-322` + `review_chair.py:172` | Chair 综合，非统计多数 |
| 最终全文裁决先于章节映射 | `ReviewSynthesis` 结构 | `global_review` 与 `chapter_evaluation` 并存 |
| Step4/5/6/7 兼容 | `compatibility_gate` + `Compatible*` 模型 | 节点7硬校验 + Schema 层 |

## 十三、接入真实能力

当前 `DebateWorkflow.default()` 全部装配 **Demo 实现**，确定性运行，目的只是**验证闭环和原 Step 4/5 兼容性**。接入真实生产时只需替换 6 个实现类（真实 LLM Specialist、Chair、两个 RAG、原系统 Adapter 和持久化任务存储），只要遵守 `ports/` 接口，`workflows/`、`schemas/` 和兼容测试都无需重写。

一句话总结：**独立评审 → 争议汇总 → 一轮定向 Debate（按需检索证据）→ 全文裁决 → 兼容输出原流程**，全程用 Schema 强制"结论必须基于证据"。
