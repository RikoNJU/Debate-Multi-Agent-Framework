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
real  真实 LLM 驱动的 Agent，用于真实服务集成与评审验证
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

真实模式的模型调用数为 `6 + 定向问题数`：3 份独立初审、1 次争议计划、每个
定向问题 1 次回应、1 次综合裁决和 1 次 Step 7 评分。同步模型客户端会在线程池中
并发运行，不阻塞 Web 事件循环。

### 复用旧 MinerU 与历史建议库

安装接入依赖：

```powershell
pip install -e ".[web,ingestion,rag]"
```

MinerU 可以直接沿用旧项目的 `MINERU_TOKEN`，也可以使用优先级更高的
`DEBATE_MINERU_TOKEN`：

```text
POST /api/debate/papers/parse   只解析 PDF，返回 Markdown 和产物列表
POST /api/debate/papers/review  解析 PDF、构建结构化论文输入并创建评审任务
GET  /api/debate/runs/{task_id} 查询任务状态和最终结果
```

`/papers/review` 使用 multipart 表单上传 `pdf`，并要求明确提供 `paper_type`
（`理论研究`、`方法创新` 或 `工程实现`）；可选提供 `paper_id` 和 `title`。解析器会从
MinerU Markdown 提取摘要、关键词、章节、小节和参考文献，未提供 `paper_id` 时根据
正文哈希生成稳定标识。

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

## 当前真实工作流

```text
历史建议 RAG -> Context Planner -> 三专家并发独立初审 -> Chair 争议计划
-> 外部证据检索（未配置时显式降级）-> 定向 Debate -> Chair 综合裁决
-> Step 4/5 兼容装配 -> Step 6 关键建议汇总 -> 历史评分检索（未配置时为空）
-> Step 7 十二维评分
```

真实模式不会使用 Demo 外部证据或 Demo 历史评分案例。论文内证据必须包含有效
`chapter_id`，且引用文本能在对应章节原文中定位；Step 6 或 Step 7 失败时任务会标记
失败，不会返回缺少最终分数的“成功”结果。

Step 7 的十二个维度复用自旧项目 `dev` 分支的
`step7_comprehensive_scoring.md`。当前总分采用十二项算术平均，等级映射为
`优秀 >= 90`、`良好 >= 75`、`及格 >= 60`。该规则需要在正式上线前由学院确认并版本化。

## 尚未达到生产要求的部分

- 外部学术证据检索和历史评分案例检索尚未提供真实适配器；未配置时保持为空。
- 任务状态仍保存在进程内，服务重启后不会恢复，且没有独立任务队列。
- 尚无认证授权、院系隔离、审计日志、人工复核和申诉流程。
- 尚无经过脱敏真实论文验证的解析准确率、评分校准和多智能体回归评测报告。
- 正式评分规则、权重、等级阈值和否决条件仍需南京大学人工智能学院确认。
