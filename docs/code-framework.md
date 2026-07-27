# Debate 论文评审代码框架说明

## 1. 框架定位

该框架在原睿文智评流程的核心评审阶段引入多视角 Debate。三个 Specialist 独立阅读全文，Review Chair 只围绕关键争议组织一轮定向质疑，再生成原 Step 4/5 可直接消费的结果。

原 Step 1-3、Step 6 和 Step 7 通过输入及适配接口复用，不要求重构整个系统。

当前后端代码统一放在 `backend/src/debate_agent_framework/`，并在包内按职责拆分目录，避免核心逻辑堆在少数单文件里。

## 2. 运行流程

```text
原 Step 1/2/3 结果
→ Context Planner 构建统一全文上下文
→ 三个 Specialist 并行独立初审
→ Review Chair 识别冲突和证据缺口
→ 按需调用 Evidence RAG
→ 相关 Specialist 完成一轮定向回应
→ Review Chair 形成全文裁决
→ 校验并输出原 Step 4/5 兼容结构
→ 原 Step 6 汇总建议
→ 历史评分 RAG 辅助原 Step 7 校准
```

第一轮评审不共享其他 Agent 意见；没有争议的问题不进入 Debate；最终结论不采用简单多数投票。

## 3. Agent 职责

| Agent | 主要职责 |
|---|---|
| Scientific Soundness | 理论基础、方法合理性、推导和结论一致性 |
| Empirical Evidence | 实验设计、数据、Baseline、消融和可复现性 |
| Global Quality | 全文结构、章节关系、工作量和表达质量 |
| Review Chair | 争议识别、问题路由、证据综合和最终裁决 |

三个 Specialist 均保持全文视角，只是关注重点不同。

## 4. 核心模块

| 目录 | 职责 |
|---|---|
| `schemas/` | 定义上下文、独立评审、争议、回应、全文评审和兼容输出 |
| `ports/` | 定义 Specialist、Chair、RAG 与原流程适配接口 |
| `agents/` | 放置 Context Planner、三个 Specialist、Review Chair 和 Demo 实现 |
| `backend/env/` | 统一模型配置、消息格式和调用入口，避免不同 Agent 各自实现模型请求 |
| `workflows/` | 编排独立初审、证据检索、定向 Debate 和 Step 6/7，并装配默认工作流 |
| `routers/` | 提供可选 API 入口 |
| `services/` | 管理任务生命周期和运行状态 |

## 5. 兼容原流程

- 输入 `DebateReviewInput` 承接论文类型、章节语义分组和 Step 3 检索建议。
- `chapter_evaluation` 保持原 Step 4 的 `chapter_N -> chapter_data` 结构。
- `workload_evaluation` 保持原 Step 5 的五项结构评价、`summary` 和工作量评语。
- `OriginalPipelineAdapter` 用于包装原 Step 6 和 Step 7 函数。

Evidence RAG 只为关键争议补充外部依据；历史评分 RAG 只校准评分尺度，不能修改已经形成的评审事实。

## 6. 接入真实能力

生产实现需要替换 `ContextPlanner`、三个 `SpecialistAgent`、`ReviewChair` 和 `OriginalPipelineAdapter`，并按需接入 `EvidenceRetriever` 与 `HistoricalScoreRetriever`。

## 7. 运行

```powershell
conda activate langgraph
cd D:\debate-multi-agent-framework
pip install -e ".[dev,web]"
debate-demo --input examples\review_input.json --output output\result.json
```

`debate-demo` 使用确定性 Demo Agent，只验证协作和兼容接口。
