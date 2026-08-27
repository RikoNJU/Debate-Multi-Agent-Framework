# 睿文智评论文评审系统

基于 **FastAPI、LangGraph、MinerU 和混合 RAG** 的可追溯多智能体论文评审系统。系统将论文解析为可定位的结构化证据，由三位 Specialist 独立初审、Review Chair 组织定向讨论并裁决，再生成修改建议和可解释的 18 维评分。

项目包含可运行的学生端、教师端和教务端，当前重点是人工智能学科论文评审，并通过声明式 Review Skill 为后续扩展学科与论文类型保留边界。

![论文评审工作流](assets/workflow.svg)

## 核心能力

- **结构化 PDF 解析**：MinerU VLM/OCR 生成 Markdown、`content_list.json` 和图表资源，保留章节、页码、坐标、公式及稳定 block/chunk ID。
- **多智能体评审**：三位 Specialist 独立分析（并发度可配置），Chair 识别冲突、发起定向 Debate 并完成证据化裁决。
- **LangGraph 编排**：三专家使用图级 Fan-out/Fan-in，定向讨论使用条件边和动态 `Send`，并保留节点进度、检查点与降级路径。
- **混合历史建议 RAG**：对已确认 Finding 执行 Dense + BM25 召回、RRF 融合与 Reranker 精排，历史建议只辅助修改方案，不参与评分。
- **可解释评分**：复用旧项目三类论文标准，将结构指标和 12 个语义指标汇总为 18 维等级，再通过确定性规则计算总分。
- **学科 Review Skill**：通过 JSON、Markdown 和 TXT 组合基础规则、学科规则、论文类型 Overlay、专家角色及检索配置，并冻结解析后的配置快照用于审计。
- **人工复核闭环**：教师在系统初评分上调整并提交终审，教务负责账号、分配、发布、统计、审计和评审表导出。
- **本地持久化**：SQLite 保存业务数据和 LangGraph 检查点，本地文件系统保存 PDF、MinerU 产物及评审结果。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 后端/API | Python 3.11、FastAPI、Pydantic、SQLAlchemy、Alembic |
| Agent 编排 | LangGraph、OpenAI-compatible LLM API |
| PDF 解析 | MinerU VLM/OCR、`content_list.json` |
| RAG | Chroma、Qwen3-Embedding-8B（4096维）、Jieba BM25、RRF、Qwen3-Reranker-0.6B |
| 数据 | SQLite、本地文件存储、LangGraph SQLite Checkpoint |
| 前端 | React 18、TypeScript、Vite、Tailwind CSS |
| 测试 | Pytest |

## 完整工作流

```text
上传 PDF
  -> MinerU 结构化解析
  -> Step 1 论文类型分类
  -> Step 2 章节阶段分类
  -> 加载并冻结 ResolvedReviewProfile
  -> Context Planner 构造共享论文证据
  -> 三位 Specialist 独立初审
  -> Chair 规划争议与定向讨论
  -> Specialist 补充证据，Chair 二次裁决
  -> Step 5 结构、规范与工作量评价
  -> 已确认 Finding 触发历史建议混合 RAG
  -> Step 6 汇总关键修改建议
  -> Step 7 生成 18 维评分与总分
  -> 教师复核，教务发布
```

三位 Specialist 共享同一份只读论文上下文和 Review Skill，但初审意见彼此隔离；只有 Chair 能看到全部初审结果并决定讨论问题。负面判断必须关联可定位 Finding，证据不足且讨论后仍无法确认的判断会被排除并降低置信度，不再产生虚假的 `human_review` 状态。

### 三位 Specialist

三个机器 Role 保持固定，Review Skill 再根据论文类型加载不同的 `persona_id`、检查项和 Prompt：

| 固定 Role | 基础职责 | 理论研究 | 方法创新 | 工程实现 |
| --- | --- | --- | --- | --- |
| `scientific_soundness` | 判断研究问题、方法逻辑与结论是否成立 | 理论定义与证明有效性专家 | 方法设计与创新性专家 | 需求与系统架构专家 |
| `empirical_evidence` | 检查实验、数据、指标及可复现性 | 理论验证与反例专家 | 实验设计与证据专家 | 系统测试与性能专家 |
| `global_quality` | 检查贡献一致性、写作结构与整体质量 | 理论贡献与论述结构专家 | 方法贡献与写作结构专家 | 工程完整性与文档专家 |

这种设计保证 LangGraph 节点和评分映射稳定，同时允许同一个专家角色针对不同论文类型改变关注重点。

## PDF 与 Chunk

系统不把整篇 PDF 提取成纯文本后按固定 Token 粗切。新论文先经 MinerU 恢复版面和阅读顺序，再从 `content_list.json` 构造带页码、坐标、内容类型和章节归属的结构化 Block；Markdown 解析仅作为降级路径。

历史建议库采用“一个评审问题一个 Chunk”的语义粒度，典型字段包括问题位置、上下文、修改建议、分析过程和原文证据。公式、HTML、图片路径等噪声会从检索文本中清理，完整原文仍保留在 Canonical JSONL 中供审计。

```text
Canonical JSONL
  |-- dense_text  -> Qwen3-Embedding-8B -> Chroma Dense V2
  |-- bm25_text   -> Jieba 分词         -> BM25 V2
  `-- rerank_text -> Qwen3-Reranker

Dense Top 5 + BM25 Top 5 -> RRF Top 3 -> Reranker
```

当前 V2 构建脚本从旧系统已经形成的七字段历史问题 Chunk 迁移数据，并不会自动把新评审写回知识库。真实论文、历史库和生成索引均不提交 Git。

## 用户入口

- 学生端：`/student`，无需注册登录，支持单篇或批量上传，并在当前浏览器查看自己的任务。
- 工作人员端：`/login`，教师与教务统一登录，进入 `/workspace`。
- 教师端：查看分配的论文、原始 PDF、AI 证据和初评分，保存草稿并提交终审。
- 教务端：管理账号和分配，发布终审结果，查看统计、审计记录并导出评审表。

## 快速启动

### 1. 后端

```powershell
conda activate langgraph
pip install -e ".[dev,web,ingestion,rag]"
Copy-Item backend/.env.example backend/.env
python -m debate_agent_framework.main
```

后端运行于 `http://localhost:8020`，健康检查为 `GET /api/debate/health`。

`backend/.env` 至少需要按运行方式配置：

```env
DEBATE_RUNTIME=real
DEBATE_API_KEY=your-model-api-key
DEBATE_MINERU_TOKEN=your-mineru-token

DEBATE_BOOTSTRAP_ADMIN_USERNAME=admin
DEBATE_BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-strong-password
```

首次启动创建管理员后，应从环境文件移除引导密码。未配置真实服务时，可将 `DEBATE_RUNTIME` 设为 `demo` 验证工作流结构。

### 2. 前端

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:3000`。Vite 会将 `/api` 请求代理到 `http://localhost:8020`。

### 3. 测试与 CLI

```powershell
pytest
debate-demo --input examples\review_input.json --output output\result.json
python -m debate_agent_framework.cli --runtime real --input examples\review_input.json --output output\result_real.json
```

## 项目结构

```text
backend/src/debate_agent_framework/
  agents/          Specialist、Chair、评分与兼容适配器
  ingestion/       MinerU 接入和结构化论文解析
  review_skills/   声明式学科及论文类型 Skill
  workflows/       LangGraph 状态与工作流节点
  services/        任务、持久化和历史建议服务
  routers/         论文、任务、认证、教师和教务 API
  persistence/     SQLAlchemy 模型、Repository 与迁移
frontend/          学生端和工作人员端 React 应用
scripts/           历史建议清洗、Dense/BM25 构建与评测工具
tests/             工作流、RAG、评分、权限与持久化测试
docs/              架构、运行手册和未实施方案
```

## 实现边界

以下能力已有代码，但尚不能宣称达到生产验证标准：

- 历史建议 V2 的清洗、索引构建、混合召回和评测工具已实现，但仓库不包含敏感语料、生成索引和人工相关性标注，尚无可报告的召回率提升数据。
- MinerU 解析、评分稳定性和多智能体效果尚未在脱敏的大规模真实论文集上形成完整评测报告。
- 服务重启后可查询已持久化任务，运行中的任务会标记为 `interrupted`，但尚无独立任务队列和自动恢复执行能力。
- 已实现基础教师/管理员权限和审计，尚无多组织隔离、细粒度数据权限及申诉流程。
- 当前可用 Review Skill 主要围绕人工智能学科；其他学科目录只应视为扩展结构或草案，不能视为已完成适配。

## 文档

- [工作流说明](docs/workflow-guide.md)
- [代码框架](docs/code-framework.md)
- [本地持久化](docs/local-persistence.md)
- [评分稳定性设计](docs/scoring-reproducibility.md)
- [历史建议 RAG V2 运行手册](docs/historical-advice-rag-v2-runbook.md)
- [历史建议 RAG 改造方案](docs/historical-advice-rag-redesign.md)
- [学科 Review Skill 方案](docs/discipline-review-skills-proposal-v1.md)

## 数据安全

论文、解析产物、历史建议语料和索引可能包含敏感信息。提交代码前必须确认这些数据未进入 Git，并在用于 RAG、评测或微调前完成匿名化、授权和数据治理检查。
