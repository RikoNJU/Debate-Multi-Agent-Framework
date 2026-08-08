# Debate 论文评审 Multi-Agent 框架

本项目是一个面向论文评审任务的 Evidence-Grounded Debate Multi-Agent 后端代码框架。它通过三个全文视角 Specialist、Review Chair 和一轮定向 Debate，在保持原 Step 4/5 输出兼容的前提下提升评审质量。

## Multi-Agent 设计简介

Multi-Agent 不是简单顺序调用多个 Prompt，而是把复杂任务拆给多个职责明确的智能体，并设计它们之间的信息共享、任务协作、冲突处理和结果验证机制。

在论文评审任务中，单一模型容易遗漏问题，或者把“章节理解、质量判断、结构评价、评分影响、修改建议”混在一起。该框架将核心评审拆为：

```text
Context Planner 负责构造评审上下文
Specialist Agents 负责多视角独立初审
Review Chair 负责识别争议、路由问题和最终裁决
Evidence Retriever 负责为关键争议补充外部证据
Original Pipeline Adapter 负责复用原 Step 6/7
```

这样的设计避免用简单多数投票代替判断，而是让最终结论基于证据、争议回应和兼容性校验。

![Debate 评审流程](assets/workflow.svg)

## 代码框架简介

框架采用后端工程结构：

```text
backend/src/debate_agent_framework/
```

包内按职责拆分为 Agent、Workflow、Schema、Port、Service、Router 等目录。核心思想是：

- `schemas/` 定义评审输入、争议、证据、回应和兼容输出结构；
- `ports/` 定义 Specialist、Chair、RAG 和原流程适配接口；
- `agents/` 放具体 Agent 或 Demo 实现；
- `backend/env/` 统一模型配置、消息格式和调用入口；
- `workflows/` 编排独立初审、证据检索、定向 Debate 和 Step 6/7，并提供默认工作流装配入口；
- `services/` 管理任务生命周期；
- `routers/` 提供 API 入口。

这种结构可以让后续开发者在不重写整体流程的前提下，逐步替换真实 LLM、Evidence RAG、历史评分 RAG 和原睿文智评 Step 6/7 适配器。

## 项目结构

```text
backend/src/debate_agent_framework/    后端源码，按职责拆分 Agent、模型、工作流、接口和服务
frontend/                              预留前端资源
examples/                      示例评审输入
tests/                         工作流与接口测试
docs/                          设计方案和代码说明
assets/                        流程图
```

## 运行

```powershell
conda activate langgraph
cd D:\debate-multi-agent-framework
pip install -e ".[dev,web]"
pytest
debate-demo --input examples\review_input.json --output output\result.json
```

可选接口启动命令：

```powershell
python -m debate_agent_framework.main
```

### Demo 与真实模型运行模式

项目支持两种运行模式，通过 `--runtime` 或环境变量 `DEBATE_RUNTIME` 切换：

```text
demo  确定性 Demo Agent，用于测试和回归基线（默认）
real  真实 LLM 驱动的 Agent，用于生产评审
```

- [代码框架说明](docs/code-framework.md)
- [代码框架详细说明](docs/code-framework-detailed.md)
- [V0 设计方案](docs/design-v0.md)

当前默认工作流装配确定性的 Demo Agent，只用于验证框架闭环和原 Step 4/5 兼容输出。
真实模式通过 `DebateWorkflow.real()` 使用真实 Specialist 与 Review Chair，并自动读取
`backend/.env`（参照 `backend/.env.example` 配置 `DEBATE_API_KEY`、`DEBATE_BASE_URL`、
`DEBATE_MODEL`）。

```bash
# 真实模型评审
python -m debate_agent_framework.cli --runtime real \
  --input examples/review_input.json --output output/result_real.json
```

真实模式会调用模型约 8 次（3 份独立初审、争议计划、定向回应、综合裁决）。

### 复用旧 MinerU 与历史建议库

安装接入依赖：

```powershell
pip install -e ".[web,ingestion,rag]"
```

MinerU 可以直接沿用旧项目的 `MINERU_TOKEN`，也可以使用优先级更高的
`DEBATE_MINERU_TOKEN`。上传解析接口为 `POST /api/debate/papers/parse`。

历史建议 RAG 可以直接读取旧项目运行时 Chroma 库：

```env
PAPER_REVIEW_BACKEND_ROOT=D:\paper-review-backend
CLOUD_API_KEY=your-dashscope-key
DEBATE_RUNTIME=real
```

系统会从旧仓根目录推导
`backend/data/databases/user_result_cloud`，并查询：

```text
user_result_content_collection_cloud_4b
user_result_format_collection_cloud_4b
```

旧库由 DashScope `text-embedding-v4`、2048 维向量建立。除非旧库本身已经重建，
不要修改 `DEBATE_EMBEDDING_MODEL` 或 `DEBATE_EMBEDDING_DIMENSIONS`，否则查询向量
会与库内向量不兼容。也可以用 `DEBATE_RAG_CHROMA_PATH` 显式指定 Chroma 目录。
