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

包内按职责拆分为 Agent、Workflow、Model、Port、Adapter、Service、Router 等目录。核心思想是：

- `models/` 定义评审输入、争议、证据、回应和兼容输出结构；
- `ports/` 定义 Specialist、Chair、RAG 和原流程适配接口；
- `agents/` 放具体 Agent 或 Demo 实现；
- `workflows/` 编排独立初审、证据检索、定向 Debate 和 Step 6/7，并提供默认工作流装配入口；
- `services/` 管理任务生命周期；
- `routers/` 提供 API 入口。

这种结构可以让后续开发者在不重写整体流程的前提下，逐步替换真实 LLM、Evidence RAG、历史评分 RAG 和原睿文智评 Step 6/7 适配器。

## 项目结构

```text
backend/src/debate_agent_framework/    后端源码，按职责拆分 Agent、模型、工作流、接口和适配器
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

- [代码框架说明](docs/code-framework.md)
- [代码框架详细说明](docs/code-framework-detailed.md)
- [V0 设计方案](docs/design-v0.md)

当前 Adapter 装配确定性的 Demo Agent，只用于验证框架闭环和原 Step 4/5 兼容输出。
