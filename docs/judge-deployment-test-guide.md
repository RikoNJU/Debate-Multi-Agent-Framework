# 衡文云审 · AI 论文评审系统 —— 评委部署与测试说明

> 本文档面向比赛评委，说明如何在本机/服务器上部署系统并完成核心功能验收。整个过程约 30~60 分钟可完整走通。

---

## 1. 系统简介

「衡文云审」是一套 **面向本科毕业论文的 Evidence-Grounded Multi-Agent 智能评审系统**，覆盖 **上传 → 解析 → 类型识别 → 多专家辩论评审 → 教师复核 → AIGC 检测 → 报告导出** 的完整闭环。

核心亮点：

| 模块 | 说明 |
|---|---|
| Multi-Agent 评审 | 3 个全文视角 Specialist 独立初审 + Review Chair 裁决 + 定向 Debate，结论基于证据与章节原文 |
| 18 维评审表 | 按全日制本科毕业论文标准（格式/选题/水平/能力/质量 共 18 项）自动打分并给出等级（优秀≥85 / 良好≥70 / 一般≥60 / 较差<60） |
| 论文类型分类 | Step 1 自动识别论文类型并匹配对应评审技能（支持多个学科/技能包可插拔） |
| 历史建议 RAG | 稠密向量 + BM25 双路召回 → RRF 融合 → 重排，为评分提供可解释的历史参照 |
| AIGC 检测 | 自研微调模型（AIGC_detector_zhv3）逐块/逐章检测 AI 生成内容，输出色块标注 PDF 与检测报告 |
| 自研微调 | QLoRA 微调 Qwen3-8B 全局质量评审技能，提升评审尺度的一致性 |
| 教师工作台 | 人工复核、回写终审分数、批量导出评审表 PDF、导出 CSV |

---

## 2. 部署环境要求

| 项 | 最低要求 | 备注 |
|---|---|---|
| 操作系统 | Linux（本说明以 CentOS/Ubuntu 为例） | Windows 可等价执行 |
| CPU / 内存 | 8 核 / 32 GB | 建议 16 核 |
| GPU | NVIDIA 显卡 ≥ 8 GB 显存，CUDA ≥ 11.8 | **AIGC 检测**用；其余模块 CPU 即可 |
| 磁盘 | 30 GB 可用空间 | 数据集/模型/PDF 产物较大 |
| Python | 3.11+ | 后端 |
| Node.js | 18+ | 前端 |
| 网络 | 可访问 `api.siliconflow.cn`、`mineru.net` | 第三方 LLM / MinerU 解析均走云端 API |
| 第三方账号 | SiliconFlow（LLM）+ MinerU（PDF 解析）账号，**均需开通且账户有余额** | 见 §9 注意事项 |

---

## 3. 部署步骤

### 3.1 目录结构

```text
qhz/
├── backend/                 # 后端 FastAPI 应用
│   ├── .env                 # 全部配置（模型、MinerU、AIGC、账号）
│   └── src/debate_agent_framework/
│       ├── agents/          # Specialist / Chair / 分类 / 评分 等智能体
│       ├── models/          # 统一模型调用入口（OpenAI 兼容协议）
│       ├── workflows/       # 独立初审、证据检索、定向 Debate、评分编排
│       ├── services/        # 任务生命周期、论文持久化、评审表导出
│       ├── routers/         # API 路由（papers / runs / auth / teacher / admin / student / aigc）
│       └── ingestion/       # MinerU 云端 PDF 解析适配器
├── frontend/                # React + Vite 前端（学生/教师/管理员三端口 + 首页）
├── finetuning/              # QLoRA 微调（Qwen3-8B 全局质量评审技能）产物与脚本
├── scripts/                 # RAG 构建/验证、重跑、评测等工具脚本
└── backend/data/            # SQLite 数据库、论文数据、解析产物
```

### 3.2 后端部署

```bash
cd /path/to/qhz

# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web,ingestion,rag,aigc]"

# 2. 准备配置（按需填入真实 Key）
cp backend/.env.example backend/.env   # 若无示例则直接编辑 backend/.env

# 3. 启动后端（端口 8020）
set -a; source backend/.env; set +a
PYTHONPATH=backend/src .venv/bin/python -m uvicorn \
  debate_agent_framework.main:app --host 0.0.0.0 --port 8020
```

> 关键配置项（`backend/.env`）：`DEBATE_MODEL`（评审主模型）、`DEBATE_API_KEY`、`DEBATE_BASE_URL`；`DEBATE_MINERU_TOKEN`（PDF 解析）；`DEBATE_AIGC_DEVICE`（AIGC 检测 GPU，如 `cuda:0`）；`DEBATE_BOOTSTRAP_ADMIN_USERNAME/PASSWORD`（初始管理员，默认 `admin`）。

### 3.3 前端部署

```bash
cd /path/to/qhz/frontend
npm install          # 仅首次
npm run dev          # 开发模式，端口 3000（vite 已将 /api 代理到 http://localhost:8020）
```

评委访问：浏览器打开 `http://<服务器IP>:3000`

---

## 4. 一键健康检查

后端起来后，执行下列命令确认各组件就绪：

```bash
# 系统健康
curl http://localhost:8020/api/debate/health
# 期望返回：{"status":"running","application":"Debate 论文评审 Multi-Agent","workflow":"debate"}

# AIGC 检测服务（首次调用会加载本地模型，约 10~30 秒）
curl http://localhost:8020/api/debate/aigc/availability
# 期望返回：{"enabled":true,"ready":true,...}
```

---

## 5. 测试账号与角色

| 角色 | 入口 | 账号来源 |
|---|---|---|
| 学生 | 首页 → 学生评审 `/student` | 无需登录即可提交论文体验（演示用） |
| 教师 | 首页 → 教师工作台 `/workspace/reviews` | 管理员创建 |
| 管理员 | 首页 → 管理后台 `/workspace/admin` | 初始 `admin`（密码见 `DEBATE_BOOTSTRAP_ADMIN_*`，部署时可改） |

管理员登录后可创建教师账号、查看全部论文/任务/统计/审计日志。

---

## 6. 功能验收流程（大赛演示路线）

### 场景 A：学生端 —— 一篇论文的完整智能评审（主流程，建议 15 分钟）

1. 打开 `http://<IP>:3000`，进入「学生评审」；
2. 上传一篇毕业论文 PDF（文本型 PDF 最佳，LaTeX/Word 导出均可，见 §7 样例）；
3. 系统自动执行并逐步展示：
   - **Step 1 论文类型分类**：自动识别类型（如 理论研究/方法创新/工程实现/法学教义分析 等）；
   - **Step 2–3 结构化解析**：章节拆分、上下文构建（基于 MinerU Markdown）；
   - **Step 4 多专家初审**：3 个专家视角并行独立评审；
   - **Step 5 定向辩论与裁决**：Review Chair 识别争议、路由问题、回引证据、最终裁决；
   - **Step 6 全维度评分**：生成 18 维评分 + 等级 + 逐章评语 + 修改建议；
4. 学生详情页可查看：任务状态（running/succeeded/failed）、专家意见、争议与裁决、18 维评审表、修改建议，并支持失败重试。

> 预期耗时：解析 1~5 分钟（MinerU）+ 评审 5~15 分钟（真实 LLM）。总时长不超过 20 分钟，期间页面实时刷新任务状态。

### 场景 B：教师端 —— 人工复核与评审表导出（建议 10 分钟）

1. 管理员创建教师账号 → 教师登录；
2. 在教师工作台选择一篇已评审论文，查看 AI 评审结果与 18 维评分表；
3. 教师**人工复核**：可调整各维评分/总分并回写终审成绩；
4. 导出**评审表 PDF**（LaTeX 生成，含等级勾选项、总评、修改建议）；
5. 管理员端导出全部评审结果 CSV。

> 预期产物：`review-table.pdf`（单篇）与 `reviews.csv`（批量）。

### 场景 C：AIGC 检测 —— AI 生成内容识别（建议 10 分钟）

1. 进入「AIGC 检测」页；首次点击检测时自动加载本地模型（约 10~30 秒）；
2. 上传同一篇论文 PDF，选择按章节/分块检测；
3. 查看检测报告：总体 AI 占比、逐块 AI 概率 / 置信度 / 判定（AI生成 vs 人工写作）、色块标注 PDF（绿=人工，红=高概率 AI）。

> 预期产物：检测报告 + `*_aigc_annotated.pdf` 色块标注版。

### 场景 D：多学科/多论文类型适配（可选，5 分钟）

- 上传非人工智能类论文（如法学），系统在 Step 1 自动路由到对应评审技能包（`review_skills/disciplines/` 可插拔），无需改核心代码。

### 场景 E：可解释性 —— 评分依据与历史参照（可选）

- 评审结果中可查看每条高严重度问题关联的论文原文证据；
- 评分页引用「历史建议」双路检索（稠密 + BM25 → RRF → 重排）的召回来源，做到评分可解释、可回溯。

---

## 7. 测试样例与预期结果对照

系统已内置可复测的论文样本（`backend/data/papers/*/source.pdf`），评委也可自带论文验证。

| 检查项 | 预期结果 |
|---|---|
| 上传非 PDF 文件 | 返回 400「仅支持 PDF 文件」 |
| 上传正常 PDF | 202 受理，返回 task_id / paper_id |
| 任务状态轮询 | running → succeeded；失败可用「重试」 |
| 评审产物 | 18 维分（0-3×18）+ 总分 + 等级 + 逐章评语 + 修改建议 |
| 评审表导出 | `review-table.pdf` 正常生成，含等级勾选 |
| AIGC 检测 | 返回总体 AI 率 + 逐块概率 + 标注 PDF |
| 健康检查 | §4 两个接口均就绪 |

---

## 8. 常见问题排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `402 {"code":30001,"message":"Sorry, your account balance is insufficient"}` | SiliconFlow 模型账户余额不足 | 充值或更换 `DEBATE_API_KEY`；此错误不会自动重试 |
| `MinerU extraction failed: parsing failed, please try again later` | MinerU 云端瞬时解析失败（多发生于较大 PDF） | 稍后重试；或拆分为两个 ≤20 页 PDF 分别解析后合并 |
| `MinerU extraction ... timed out` | MinerU 云端处理超时 | 调大 `DEBATE_MINERU_TIMEOUT_SECONDS`（默认 600s）后重试 |
| `invalid device ordinal` | `DEBATE_AIGC_DEVICE` 指向了不存在的 GPU | 检查 `nvidia-smi`，改为存在的卡号（如 `cuda:0`） |
| 评审表导出 500 / 超时 | LaTeX（tectonic）首次编译需下载宏包 | 调大 `DEBATE_REVIEW_TABLE_TIMEOUT`（默认 900s），重试 |
| `/v1/models` 正常但 chat 报 402 | Key 有效但无余额 | 充值后重启后端 |
| 根磁盘满导导致服务异常 | 数据/模型占满系统盘 | 将 `backend/data`、HuggingFace 缓存放到大数据盘 |

---

## 9. 注意事项（评委评审前务必确认）

1. **第三方付费 API**：评审主流程依赖 SiliconFlow（LLM）与 MinerU（PDF 解析），均按量计费。**演示前确认账户有余额与配额**，评审期间避免并行同时提交多篇（会同时消耗配额）。
2. **评审耗时**：单篇约 5~20 分钟。建议评委按「先上传 → 讲解架构 → 回来看结果」的方式安排演示节奏。
3. **GPU**：AIGC 检测需 GPU（模型加载约需 1 GB 显存）；模型权重已本地缓存，**无需科学上网**即可冷启动。
4. **先跑通再展示**：建议演示前先用内置样例论文完整跑通一遍，避免评审现场撞上瞬时失败。