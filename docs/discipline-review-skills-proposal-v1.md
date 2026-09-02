# 多专业论文评审 Skill 插件化方案（V1.1 讨论稿）

## 文档状态

- 状态：V1.2 已完成 AI 可扩展框架；第二专业内容与远程真实索引联调仍待实施
- 原始记录日期：2026-08-21
- 当前目标：将现有偏人工智能专业的论文评审系统改造成“核心引擎固定、专业能力可插拔”的多专业评审架构
- 第一阶段范围：专业 Skill、专业内论文类型 Skill、论文类型与章节阶段分类、专业化专家 Prompt / Checklist、固定评分小项、专业规则、术语、历史建议 RAG 路由、版本与审计
- 第一阶段不做：任意代码形式 Skill、动态 Agent 数量、专业独立 Embedding 模型、专业独立 Reranker 模型、自由模型路由、复杂跨专业 Skill 冲突求解、Skill Marketplace

### 2026-08-25 V1.2 实施记录

已实现并通过自动化测试：

- 严格 JSON Skill Schema、受限文件 Loader、Registry/Resolver 和确定性 Profile Hash；
- 独立的 AI Discipline Common，以及差异化的 `theory / method / engineering` Persona、Prompt、Checklist、Rubric、规则、章节体系、Chair Guidance 与 RAG Overlay；
- LangGraph 在 Step 1 前解析 Discipline Profile，在 Step 1 后解析并冻结强类型 `ResolvedReviewProfile`，三个 Specialist、Chair、Rubric 和 RAG 读取同一快照；
- Step 1 从 Discipline Common 读取允许类型、信号和分类 Prompt，Step 2 从类型 Skill 读取章节标签、确定性规则和分类 Prompt；
- 专业级 Retriever Registry 负责物理 Dense/BM25 路由，论文类型 Overlay 实现 metadata `prefer/strict`、RRF 加权和 Rerank Query Instruction；
- Skill 选择参与评审复用指纹；任务表独立回写实际 `skill_id`、版本、Profile Hash 与组合版本快照，最终结果保留完整 Profile。

当前不能宣称已经完成：

- 尚未接入第二专业，当前 API 仍只可运行人工智能兼容的三种旧 `PaperType`；
- Registry 路由机制已经实现，但当前环境只注册一套 AI Retriever；Law 仅为不可运行的 Draft，尚无第二套真实语料和索引可供切换验证；
- AI 三类 Overlay 已接入检索逻辑，但远程 Dense V2/BM25 V2 真实索引不在本机，本次只能完成契约与自动化测试，尚未做真实召回效果对照；
- 专业术语已随 Profile 加载，但尚未据此构建新的 BM25 索引，运行时不会热改现有索引分词；
- 低置信度论文类型的人工确认流程尚未实现；当前会记录置信度，但不会中断等待人工选择；
- 尚未完成 AI 与第二专业的基准评测、灰度发布和管理端 Skill 选择界面。

---

## 一、总体思想

本方案采用类似dsh插件化 / SkillTree 的思想：

> **系统主体保持稳定，不同专业、不同论文类型的评审能力通过 Skill 动态加载。**

目标不是为每个专业复制一套完整后端，而是让同一套评审工作流根据论文专业和论文类型切换：

```text
专家关注点
专业规则
证据要求
专业术语
历史建议向量库
BM25 库
Rerank Prompt
未来可选 LoRA / 专业模型
```

最终运行关系：

```text
通用评审引擎
+
Base Skill
+
专业 Common Skill
+
该专业下的论文类型 Skill
+
本次论文数据
=
ResolvedReviewProfile
```

---

## 二、为什么要做插件化

当前系统的评审规则明显偏人工智能 / 计算机论文。

例如方法创新型 AI 论文通常强调：

```text
baseline comparison
ablation study
dataset
metric
reproducibility
```

但法学论文可能强调：

```text
法条解释
判例支撑
规范分析
概念界定
```

管理学论文可能强调：

```text
研究假设
样本代表性
量表信效度
统计检验
```

数学论文又可能强调：

```text
定义
定理
证明闭合
边界条件
```

如果直接为每个专业复制一套 Workflow：

```text
AI Workflow
Law Workflow
Management Workflow
Math Workflow
```

会产生：

- 重复代码；
- 规则分叉；
- 难以统一维护；
- 难以统一审计；
- 专业扩展成本越来越高。

因此更合理的目标是：

```text
Core Engine 不知道具体专业知识
Skill 提供专业能力
```

---

## 三、Skill 的定义

Skill 不等于 Prompt。

本方案将 Skill 定义为：

> **可版本化、可审计、可组合的专业论文评审能力包。**

V1 中，一个 Skill 主要包含九类内容：

```text
1. 专家 Prompt / Checklist
2. 专业规则与证据要求
3. 专业术语
4. 专业内论文类型定义与分类规则
5. 章节阶段分类体系
6. 固定评审小项及其到统一评分维度的映射
7. Chair 专业裁决指引
8. 历史建议 RAG 路由
9. Rerank Query Instruction
```

因此：

```text
Skill
├── Prompt
├── Rules
├── Terminology
├── Classification Taxonomy
├── Rubric / Score Mapping
├── Chair Guidance
├── Retrieval Config
└── Metadata / Evaluation
```

而不是：

```text
Skill = 一段 Prompt
```

---

## 四、系统分层

### 4.1 Core Engine

核心引擎不随专业变化。

包括：

```text
PDF 上传
MinerU
Canonical Paper Record
任务状态管理
Workflow / LangGraph
Pydantic Schema
多 Agent 调度
持久化
日志与审计
错误处理
基础 Chair 流程
历史报告保存
```

Core Engine 不应硬编码：

```text
什么叫消融实验
什么叫判例
什么叫量表信效度
什么叫证明闭合
某专业允许哪些论文类型
某论文类型允许哪些章节阶段
某个专业检查项应由哪个专家负责
```

这些全部由 Skill 提供。

### 4.2 Base Skill

Base Skill 保存所有专业必须遵守的底层评审原则。

例如：

- 必须基于论文原文证据；
- 不得伪造证据；
- MinerU 解析失败不能直接作为论文质量问题；
- Finding 必须符合统一 Pydantic Schema；
- severity 使用统一基础定义；
- Chair 的基础裁决流程；
- 人工复核条件；
- 隐私和审计要求；
- 模型失败和回退原则。

Base Skill 必须尽可能小。

因为一条规则一旦进入 Base，就意味着所有专业默认受其约束。

---

## 五、专业 SkillTree 结构

V1 不再把 `Paper Type Skill` 设计成一个跨专业的全局目录。

更推荐：

> **论文类型 Skill 属于具体专业之下。**

原因是同名论文类型在不同专业中的评审含义并不完全一致。

例如：

```text
人工智能 - 工程实现型
```

和其他专业中的“工程型论文”，关注点并不一定一致。

因此 V1 目录建议：

```text
review_skills/
├── base/
│   ├── manifest.yaml
│   ├── evidence_rules.yaml
│   ├── severity_rules.yaml
│   └── prompts/
│
└── disciplines/
    ├── artificial_intelligence/
    │   ├── common/
    │   │   ├── manifest.yaml
    │   │   ├── common_rules.yaml
    │   │   ├── paper_types.yaml
    │   │   ├── terminology.txt
    │   │   └── prompts/
    │   │
    │   ├── theory/
    │   │   ├── manifest.yaml
    │   │   ├── specialists.yaml
    │   │   ├── chapter_taxonomy.yaml
    │   │   ├── rubric.yaml
    │   │   ├── evidence_rules.yaml
    │   │   ├── retrieval.yaml
    │   │   ├── chair_guidance.yaml
    │   │   ├── prompts/
    │   │   │   ├── specialist_a.md
    │   │   │   ├── specialist_b.md
    │   │   │   ├── specialist_c.md
    │   │   │   └── rerank.md
    │   │   └── evals/
    │   │
    │   ├── method/
    │   │   ├── manifest.yaml
    │   │   ├── specialists.yaml
    │   │   ├── chapter_taxonomy.yaml
    │   │   ├── rubric.yaml
    │   │   ├── evidence_rules.yaml
    │   │   ├── retrieval.yaml
    │   │   ├── chair_guidance.yaml
    │   │   ├── prompts/
    │   │   └── evals/
    │   │
    │   └── engineering/
    │       ├── manifest.yaml
    │       ├── specialists.yaml
    │       ├── chapter_taxonomy.yaml
    │       ├── rubric.yaml
    │       ├── evidence_rules.yaml
    │       ├── retrieval.yaml
    │       ├── chair_guidance.yaml
    │       ├── prompts/
    │       └── evals/
    │
    ├── law/
    │   ├── common/
    │   ├── doctrinal/
    │   └── case_study/
    │
    ├── management/
    └── mathematics/
```

---

## 六、为什么专业下还要有 `common/`

同一个专业的不同论文类型之间仍然存在大量共享内容。

以人工智能为例：

```text
Transformer
LLM
RAG
LoRA
Precision
Recall
F1
baseline
dataset
embedding
```

这些术语、缩写、同义词和部分通用规则，没有必要在：

```text
theory
method
engineering
```

各复制一遍。

因此运行时采用：

```text
Base Skill
+
Artificial Intelligence Common
+
Artificial Intelligence Method
```

或者：

```text
Base Skill
+
Artificial Intelligence Common
+
Artificial Intelligence Engineering
```

这样既避免重复，也保留论文类型差异。

---

## 七、论文类型 Skill 的职责

论文类型 Skill 定义：

```text
该论文类型重点关注哪些问题
三个 Specialist 分别看什么
哪些证据重要
哪些规则是 mandatory / recommended / heuristic
允许使用哪些章节阶段标签
固定评审小项如何映射到统一的 12 个语义评分维度
检索本专业历史建议时使用什么 paper_type 过滤或加权策略
Rerank Query 应强调什么
```

论文类型 Skill **不负责选择独立的物理向量库或 BM25 索引**。V1 的历史建议语料和
索引粒度固定为 `discipline`：物理 Dense Collection 与 BM25 Index 由专业 Common
Skill 声明；论文类型 Skill 只在同一专业库内提供 `paper_type` metadata 过滤、加权和
Rerank Query 侧重点。这样与当前历史库的数据粒度一致，也避免把本来就不多的案例再次
切碎。

不同专业可以拥有不同的论文类型 ID。例如人工智能继续兼容：

```text
theory / method / engineering
```

法学可以定义：

```text
doctrinal / case_study
```

Core Engine 不应继续把当前 `PaperType` 三值枚举视为所有专业的全局真理。V1
实施时应使用稳定字符串 ID 表示专业内论文类型，并把旧枚举保留为人工智能 Skill
的兼容适配层。

例如人工智能 `method`：

```text
重点关注：
- 研究问题是否明确；
- 方法贡献是否成立；
- baseline 是否合理；
- 是否有定量对比；
- 是否有必要的消融；
- 指标是否定义；
- 实验是否支持方法声明。
```

人工智能 `engineering`：

```text
重点关注：
- 系统需求是否清晰；
- 架构设计是否合理；
- 关键模块是否实现；
- 是否有功能验证；
- 是否有性能或稳定性测试；
- 工程工作量是否真实充分。
```

同样属于人工智能专业，但论文类型不同，评审重点明显不同。

---

## 八、专家设计

### 8.1 V1 不做完全动态 Agent

第一阶段不建议允许：

```text
AI = 3 个 Agent
Law = 5 个 Agent
Math = 4 个 Agent
```

否则 Workflow、状态结构、Chair 输入和评测矩阵都会迅速复杂化。

V1 建议：

> **专家槽位固定，专家职责由专业 Skill 和论文类型 Skill 专业化。**

“槽位固定”还意味着机器协议中的角色 ID 固定。V1 继续保留当前三个底层 ID：

```text
scientific_soundness
empirical_evidence
global_quality
```

专业 Skill 通过 `persona_id`、`display_name`、Prompt 和 Checklist 改变专业职责，
不能把底层 `role` 改成新的枚举值。否则会破坏现有 Pydantic Schema、Specialist
Registry、Chair 路由和 LangGraph State。

例如底层始终保留三个 Specialist 槽位。

人工智能方法型：

```text
Specialist A -> scientific_soundness
Specialist B -> empirical_evidence
Specialist C -> writing_structure
```

法学：

```text
scientific_soundness -> persona: normative_reasoning
empirical_evidence   -> persona: legal_evidence
global_quality       -> persona: legal_writing_structure
```

数学：

```text
scientific_soundness -> persona: proof_validity
empirical_evidence   -> persona: theoretical_completeness
global_quality       -> persona: mathematical_writing_structure
```

底层工作流仍然：

```text
Specialist A
Specialist B
Specialist C
      ↓
     Chair
```

这样实现专业插件化的同时，不破坏现有核心 Workflow。

---

## 九、Specialist 配置示例

```yaml
specialists:
  specialist_a:
    slot: specialist_a
    role: scientific_soundness
    persona_id: ai_method_scientific_soundness
    display_name: 科学性与方法合理性专家
    prompt: prompts/scientific_soundness.md
    checks:
      - research_problem_clear
      - method_logic_valid
      - contribution_supported

  specialist_b:
    slot: specialist_b
    role: empirical_evidence
    persona_id: ai_method_empirical_evidence
    display_name: 实验证据专家
    prompt: prompts/empirical_evidence.md
    checks:
      - dataset_description
      - baseline_comparison
      - metric_definition
      - ablation_study

  specialist_c:
    slot: specialist_c
    role: global_quality
    persona_id: ai_method_writing_structure
    display_name: 学术写作与结构专家
    prompt: prompts/writing_structure.md
    checks:
      - academic_expression
      - section_role
      - terminology_consistency
```

Prompt 是 Specialist 配置的一部分，而不是整个 Skill。

---

## 十、专业规则与证据要求

建议 Skill 内的规则强制分成三类：

```text
mandatory_rules
recommended_checks
heuristic_preferences
```

### `mandatory_rules`

正式、强制、具有明确制度或规范来源，可以影响正式结论。

### `recommended_checks`

专家应该关注，但缺失不代表可以自动判定问题成立。

### `heuristic_preferences`

仅作为分析提示，不能直接扣分或自动提高 severity。

示例：

```yaml
rules:
  - id: AI_METHOD_001
    type: mandatory
    source: "正式规范版本或文件编号"
    check: metric_definition

  - id: AI_METHOD_002
    type: recommended
    check: baseline_comparison

  - id: AI_METHOD_003
    type: heuristic
    check: recent_benchmark_comparison
```

Skill 不允许把教师个人经验或模型偏好伪装成正式强制规则。

### 10.1 论文类型与章节阶段分类

当前系统的 Step 1 只支持“理论研究、方法创新、工程实现”，Step 2 的章节阶段标签
也按这三类硬编码。多专业 V1 必须把以下内容纳入 Skill：

```text
paper_types
paper_type_classifier_prompt
chapter_stage_taxonomy
chapter_classifier_prompt
classification_version
```

专业选择先确定 Discipline Common，再在该专业允许的论文类型集合内执行 Step 1；
完成论文类型识别后，加载对应 Paper Type Skill 并执行 Step 2。分类结果必须保存来源、
置信度和版本。低置信度或与用户选择明显冲突时进入人工复核，不能静默切换专业。

### 10.2 固定 Rubric 与统一 18 维评分

V1 保持现有 18 维评分表和总分公式不变：

```text
6 个格式/结构规范维度：继续由 Core 的确定性算法计算
12 个语义维度：保持统一定义，由专业 Skill 提供专业化判断依据
```

每个 Paper Type Skill 必须提供 `rubric.yaml`，至少定义：

```text
rubric_item_id
适用章节阶段
负责的固定 Specialist role
判断标准
证据要求
映射到哪些统一语义维度
每个维度的权重
```

Skill 可以改变“用什么专业检查项评价某个维度”，但 V1 不允许改变 18 维名称、
等级边界和总分公式。这样可以兼顾专业化与跨专业报告结构的一致性。若未来证明某专业
无法合理使用现有 18 维，应新增版本化 `score_schema_id`，不能在 V1 中隐式改义。

### 10.3 Chair 专业裁决指引

Chair 的基础状态机、证据真实性要求和裁决枚举属于 Base Skill，不允许覆盖。专业 Skill
可以通过 `chair_guidance.yaml` 补充：

```text
专业证据的可信度判断
专业规则冲突时的优先级
哪些结论必须转人工复核
不同专业 Finding 的合并和区分原则
```

Chair Guidance 不能让 Chair 绕过原文证据，也不能把 recommended 或 heuristic 规则
提升为 mandatory。

---

## 十一、专业术语

专业术语优先放在专业 `common/` 中。

例如：

```text
artificial_intelligence/common/terminology.txt
```

可以包含：

```text
Transformer
大语言模型
大型语言模型
LLM
RAG
LoRA
消融实验
基线模型
Precision
Recall
F1
Embedding
```

V1 可供：

```text
jieba 分词
BM25
术语识别
Query normalization
Prompt 构造
```

共同使用。

术语表不是只影响 Prompt。只要术语进入 jieba 自定义词典，就必须使用相同术语版本
重新构建 BM25 索引，并在索引 Manifest 中记录：

```text
terminology_version
tokenizer_version
corpus_checksum
```

禁止只修改查询端分词词典而继续使用旧 BM25 索引，否则索引端和查询端分词不一致。

---

## 十二、RAG 插件化：专业级物理路由 + 论文类型逻辑路由

第一阶段不建设复杂 Retrieval Profile。

V1 将 RAG 配置分为两层：

```text
Discipline Common Skill：
1. Vector DB / Collection / Namespace
2. BM25 Index
3. 专业术语版本

Paper Type Skill：
1. paper_type metadata 过滤或加权
2. Rerank Query Instruction / Query Template
```

例如人工智能专业 Common Skill 声明共享的物理资源：

```yaml
retrieval:
  vector_collection: historical_advice_ai
  bm25_index: historical_advice_ai_bm25
  terminology_version: ai_terms_v1

  top_k:
    vector: 5
    bm25: 5
    rrf: 3
    max_per_finding: 2
```

人工智能方法创新型 Skill 只补充逻辑路由：

```yaml
retrieval_overlay:
  paper_type_filter: method
  filter_mode: prefer
  rerank_instruction: prompts/rerank_method.md
```

其中 `prefer` 表示优先同类型案例，但在案例不足时允许从同一专业的其他论文类型召回；
是否改为严格 `filter` 必须由真实语料统计和召回评测决定，不能提前写死。

上面的 TopK 与当前历史建议 RAG V2 保持一致：

```text
Dense Top 5
+ BM25 Top 5
-> RRF Top 3
-> Reranker
-> 每个 Finding 最终保留 1 至 2 条
```

即使某个论文类型案例较少，也不为其建立独立物理库。V1 固定采用：

```text
按 discipline 建立物理语料和索引
+ paper_type metadata 过滤或加权
```

只有未来数据量和离线评测证明“同专业跨类型案例持续造成明显误召回”时，才讨论拆分为
专业内论文类型独立 Collection。该拆分不属于 V1。

第一阶段暂不切换：

```text
Embedding 模型
Reranker 模型
Chunk 策略
复杂 Query Rewrite
外部学术数据库
```

---

## 十三、为什么切换向量库

不同专业中，相似的通用问题描述可能对应完全不同的历史修改建议。

例如：

```text
论证不足
```

人工智能中可能对应：

```text
缺少 baseline
缺少消融
缺少量化结果
```

法学中可能对应：

```text
缺少法条解释
缺少判例支撑
规范论证不足
```

因此专业变化时，应切换历史建议的向量检索空间。

---

## 十四、为什么切换 BM25 库

BM25 对专业关键词的精确匹配很重要。

AI：

```text
LoRA
F1
Transformer
消融实验
baseline
```

法学：

```text
司法解释
构成要件
请求权基础
比例原则
裁判要旨
```

因此：

```text
Dense Retrieval
+
BM25 Retrieval
```

都应按专业 / 专业论文类型路由。

---

## 十五、为什么切换 Rerank Query Instruction

V1 暂不切换 Reranker 模型。

统一使用同一个 Reranker，但专业 Skill 可以提供不同的 Query Instruction 或 Query
Template。

当前 Qwen3 专用 Reranker 接口接收 `query + documents`，没有独立的 Prompt 参数。
因此 V1 的实现方式是把专业指引稳定地组合进 Query，而不是向接口发送一个不存在的
`rerank_prompt` 字段。未来若切换为 LLM Reranker，再单独设计 Prompt 配置。

AI 方法型 Rerank Query Instruction 可以强调：

```text
- 是否属于相同实验设计问题；
- 是否涉及类似 baseline；
- 是否涉及相似指标或数据集；
- 是否适用于当前论文类型；
- 历史建议是否能迁移到当前方法问题。
```

法学 Query Instruction 则可以强调：

```text
- 是否属于相同法律问题；
- 是否涉及相似规范依据；
- 是否属于类似判例或规范论证问题；
- 历史意见是否适用于当前研究范式。
```

这样可以在不引入多个 Reranker 模型的前提下实现专业化精排。

---

## 十六、RAG 的责任边界

RAG 不负责发现论文问题。

正式流程仍然是：

```text
Specialist
-> 根据当前论文原文发现问题

Chair
-> 根据证据确认问题

Retrieval Router
-> 根据 discipline / paper_type 选择历史建议库

Vector + BM25
-> 混合召回

Reranker
-> 根据专业 Prompt 精排

Step 6
-> 结合当前论文生成新的修改建议
```

禁止：

```text
先从历史库检索别人有哪些问题
-> 再反推当前论文也有同样问题
```

历史建议只能辅助“如何修改已经确认的问题”，不能成为问题成立的主要证据。

---

## 十七、RAG Metadata

即使 V1 采用物理分库，每条历史建议仍建议保存：

```json
{
  "discipline_id": "artificial_intelligence",
  "paper_type": "method",
  "finding_category": "empirical_evidence",
  "source_version": "..."
}
```

这样未来可以平滑迁移为：

```text
统一向量库
+
namespace
+
metadata filter
```

而不需要重新设计数据结构。

---

## 十八、Skill 的 Prompt 怎么写

Prompt 主要承担：

```text
角色定位
专业关注点
分析方法
证据边界
输出行为
```

例如：

```markdown
你是人工智能方法创新型论文的实验证据评审专家。

重点检查：
1. 数据集来源是否说明；
2. baseline 是否合理；
3. 指标是否定义；
4. 核心性能结论是否有对应实验；
5. 对模块贡献的声明是否有相应消融证据。

注意：
- 不得仅因为没有某项常见实验就自动判定论文错误；
- 必须基于当前论文提供的证据；
- 证据不足时明确输出 evidence_insufficient；
- 输出必须符合 ReviewFinding Schema。
```

结构化、可枚举、需要版本管理的规则，应优先写进 YAML，而不是全部塞进 Prompt。

---

## 十九、Skill Manifest 示例

```yaml
skill_id: artificial_intelligence_method
discipline_id: artificial_intelligence
paper_type: method
version: 1.0.0
display_name: 人工智能 - 方法创新型论文

inherits:
  - base
  - artificial_intelligence/common

specialists_config: specialists.yaml
chapter_taxonomy: chapter_taxonomy.yaml
rubric: rubric.yaml
evidence_rules: evidence_rules.yaml
retrieval_config: retrieval.yaml
chair_guidance: chair_guidance.yaml

status: draft
```

---

## 二十、Skill 加载与解析

运行时不应该让 Workflow 到处直接读取 YAML / Markdown。

建议增加：

```text
SkillRegistry
SkillLoader
SkillResolver
```

调用：

```python
profile = skill_resolver.resolve(
    discipline="artificial_intelligence",
    paper_type="method"
)
```

最终生成统一对象：

```text
ResolvedReviewProfile
```

### 20.1 运行时装配边界

当前 LangGraph 和 Specialist Registry 在服务启动时构建，不能为每次上传重新编译一张图。
V1 应保持 Workflow 节点和三个 Agent 实例稳定，把 `ResolvedReviewProfile` 作为本次任务的
状态输入，由分类器、Specialist、Chair、Rubric 和 Retriever 在调用时读取。

```text
服务启动：加载 Registry、编译一次 LangGraph
任务开始：按 discipline + paper_type 解析 Profile
任务执行：Profile 随 LangGraph State 传递
```

RAG 资源可以按 `retrieval_route` 延迟加载并缓存，但缓存键必须包含语料、Collection、
BM25 Manifest 和术语版本，不能把上一专业的索引实例误用于下一任务。缓存键不包含
`paper_type` 物理库名称，因为 V1 不按论文类型建立物理索引；论文类型只进入检索过滤、
加权和 Rerank Query。

---

## 二十一、ResolvedReviewProfile

后续 Workflow 只依赖：

```text
ResolvedReviewProfile
```

而不关心配置来自哪些文件。

概念结构：

```text
ResolvedReviewProfile
├── discipline
├── paper_type
├── specialist_slots
├── prompts
├── paper_type_taxonomy
├── chapter_taxonomy
├── rubric_items
├── score_dimension_mapping
├── rules
├── evidence_requirements
├── chair_guidance
├── terminology
├── retrieval_route
├── prompt_versions
├── component_versions
└── resolved_profile_hash
```

`resolved_profile_hash` 必须由解析后的完整规范化配置计算，不能只依赖人工填写的
SemVer。即使维护者忘记升级版本，只要配置内容变化，Hash 也必须变化。

`model_routes` 可以作为未来保留字段，但 V1 不允许 Skill 自由切换模型。V1 所有 Skill
使用 Core 配置的统一模型路由，避免文档能力超出真实实现范围。

这样 Workflow 不会和某个具体专业目录绑定。

---

## 二十二、运行流程

```text
用户上传论文
        ↓
选择 discipline
        ↓
服务端校验 discipline_id
        ↓
加载 Base Skill
        ↓
加载 Discipline Common
        ↓
在该专业允许的类型集合内识别 paper_type
        ↓
加载该专业下的 Paper Type Skill
        ↓
SkillResolver
        ↓
ResolvedReviewProfile
        ↓
根据 profile.chapter_taxonomy 完成章节阶段分类
        ↓
构建三个专业化 Specialist
        ↓
独立评审
        ↓
Chair
        ↓
根据 profile.retrieval
选择 Vector DB + BM25
        ↓
使用专业 Rerank Query Instruction
        ↓
生成专业化修改建议
        ↓
使用 profile.rubric 映射到统一 12 个语义维度
        ↓
与 6 个确定性维度汇总为统一评分 / 报告
```

---

## 二十三、专业选择

V1 可以采用：

```text
学生选择专业
+
系统进行一致性校验
```

系统依据：

```text
标题
摘要
关键词
```

检查所选专业是否明显冲突。

如果冲突：

```text
标记
-> 教师 / 教务确认
```

长期更可靠的优先级：

```text
教务预设
>
教师确认
>
学生选择
```

---

## 二十四、跨学科论文

第一阶段不同时完整加载两个 Discipline Skill。

采用：

```text
primary_discipline
secondary_discipline_tags
```

例如：

```text
primary_discipline = law
secondary_tags = [artificial_intelligence]
```

主专业决定：

```text
主要规则
主要专家职责
主要 RAG
主要评审解释
```

辅助标签第一阶段最多用于：

```text
术语补充
检索提示
专家 checklist 补充
```

避免两个完整 Skill 同时改变核心评审逻辑。

---

## 二十五、Skill 合并规则

不能简单使用：

```text
后加载配置直接覆盖前面全部字段
```

建议将字段区分为：

### 可覆盖

```text
Prompt
recommended_checks
retrieval config
专业 persona
章节阶段分类 Prompt
```

### 可追加

```text
terminology
retrieval keywords
heuristic checks
```

### 不允许专业 Skill 覆盖

```text
隐私规则
审计规则
统一 Schema
证据真实性要求
基础错误处理规则
18 维评分名称、等级边界和总分公式
固定 Specialist 机器角色 ID
```

### 仅授权配置允许覆盖

```text
mandatory_rules
评分解释
正式制度要求
```

---

## 二十六、Skill Schema 与 Pydantic

所有 Skill 配置均通过 Pydantic Schema 校验。

例如检查：

```text
skill_id 是否存在
version 是否合法
discipline_id 是否支持
paper_type 是否合法
retrieval 是否包含必要字段
rule.type 是否属于允许枚举
Prompt 文件是否存在
继承关系是否存在循环
rubric 是否引用合法章节阶段、固定专家槽位和评分维度
mandatory rule 是否包含可审计来源
BM25 术语版本是否与索引 Manifest 一致
```

错误 Skill：

```text
加载失败
-> 不进入正式评审
-> 返回可诊断错误并记录审计事件
```

正式模式必须失败关闭，不能静默回退到 Base 或默认专业，否则系统可能使用错误标准生成
看似完整的正式报告。只有用户或教师明确选择 `generic_review` 时，才允许使用通用 Profile，
并且报告必须标记“专业规则未加载、需要人工复核”。

Loader 还必须限制文件访问边界：拒绝绝对路径、`..` 目录穿越、Skill 根目录外的符号链接、
重复 ID、未知字段和超出大小限制的 Prompt。所有 YAML 使用安全解析，不支持自定义对象标签。

Skill 不能通过自由字段改变 Core Engine 行为。

---

## 二十七、V1 Skill 不允许包含业务代码

第一阶段 Skill 仅允许：

```text
YAML
JSON
Markdown
TXT
```

不允许：

```text
Python
Shell
任意可执行脚本
```

原因：

- 避免专业 Skill 演变成多套业务代码；
- 降低安全风险；
- 更容易版本化；
- 更容易人工审核；
- 保证插件只能配置能力，不能绕过 Core Engine。

---

## 二十八、与专业微调模型的关系

Skill 和微调模型不是互斥关系。

关系：

```text
Skill
-> 定义专业任务、规则、Prompt、RAG、评测

LoRA / 专业模型
-> 作为 Skill 可以选择的一个能力依赖
```

未来可以：

```text
Artificial Intelligence Method Skill
-> writing_structure
-> Qwen3-8B + AI Writing Adapter
```

其他专家仍可使用通用模型。

但 V1 不要求每个 Skill 绑定微调模型。

---

## 二十九、版本与审计

每个任务至少保存：

```text
discipline_id
paper_type
base_skill_version
discipline_common_version
paper_type_skill_version
retrieval_vector_version
bm25_index_version
rerank_instruction_version
specialist_prompt_versions
classification_versions
chapter_taxonomy_version
rubric_version
terminology_version
resolved_profile_hash
```

还应保存解析后的 `ResolvedReviewProfile` 审计快照，或保存能够完整重建它的内容地址。
`resolved_profile_hash` 必须加入当前论文评审 `review_fingerprint`。同一 PDF 使用不同专业、
论文类型、Rubric 或 Prompt 时必须重新评审，不能复用旧结果。

否则 Skill 更新后无法解释旧报告使用了什么规则。

---

## 三十、V1 不做的事情

第一阶段暂不实现：

```text
专业独立 Embedding 模型
专业独立 Reranker 模型
复杂 Query Rewrite
多个完整 Discipline Skill 同时加载
动态 Agent 数量
用户上传 Skill
Skill Marketplace
热加载任意代码
外部数据库自动选择
复杂 Skill 冲突求解
```

这些留给后续版本。

---

## 三十一、V1 验证目标

第一阶段不是证明：

```text
可以支持所有专业
```

而是证明：

> **同一个 Core Engine 可以通过切换 Skill，在两个评审范式明显不同的专业上产生明显不同且正确的专业化行为。**

建议：

```text
专业 A：Artificial Intelligence
专业 B：Law
```

优先选择差异大的第二专业，而不是软件工程等相近专业。

如果：

```text
AI
vs
Law
```

都能够稳定共用同一套 Core Engine，则可以更有力地证明插件化架构有效。

---

## 三十二、V1 实施路线

### Phase 1：人工智能现有规则盘点

找出当前代码中：

```text
Prompt
if/else
hardcoded checklist
Step 1 论文类型
Step 2 章节阶段
chapter rubric 与 12 维映射
Chair 专业判断
RAG index
BM25
术语
评分解释
```

哪些实际上属于 AI 专业。

产出“当前代码位置 -> Skill 配置字段 -> 是否属于 Core”的迁移矩阵，未完成盘点前不开始
批量移动 Prompt。

### Phase 2：建立 Skill Schema 与安全约束

先定义并测试：

```text
manifest
specialist slots / personas
paper type taxonomy
chapter taxonomy
rubric / score mapping
rules / evidence requirements
chair guidance
retrieval route
terminology
prompt reference
versions / resolved profile hash
```

同时实现循环继承、非法路径、未知字段、无来源 mandatory rule、非法评分映射等失败用例。

### Phase 3：建立 AI SkillTree

先建立：

```text
artificial_intelligence/
├── common/
├── theory/
├── method/
└── engineering/
```

要求：

> 提取前后评审行为尽可能保持一致。

这是无损重构阶段。

### Phase 4：实现 Skill Registry / Loader / Resolver

形成：

```python
profile = resolve(
    discipline,
    paper_type
)
```

输出：

```text
ResolvedReviewProfile
```

Resolver 必须输出确定性的规范化结果和 `resolved_profile_hash`。同样输入重复解析应产生
完全相同的 Profile 和 Hash。

### Phase 5：Shadow 模式接入 LangGraph

先把 `ResolvedReviewProfile` 放入本次任务的 LangGraph State，但正式输出仍由旧路径生成。
Shadow 模式记录新旧分类、Prompt、Checklist、Rubric 和 Retrieval Route 的差异，不影响
学生报告。AI Skill 达到行为等价后再切换正式路径。

### Phase 6：接入分类、Specialist、Chair 与 Rubric

三个 Specialist 从：

```text
ResolvedReviewProfile.specialist_slots
```

读取：

```text
role
persona_id
Prompt
checklist
evidence rules
```

Step 1/2 同时改为读取专业论文类型和章节阶段体系；Chair 读取专业裁决指引；固定评审小项
读取 `rubric_items` 并映射到统一 12 维。V1 不改变 6 个确定性维度和总分公式。

### Phase 7：接入 RAG 路由

V1 只支持：

```text
专业级 vector collection
专业级 BM25 index
论文类型 metadata 过滤或加权
论文类型 rerank query instruction
```

保持 `Dense 5 + BM25 5 -> RRF 3 -> 每个 Finding 1 至 2 条`。采用专业级物理索引和
论文类型 metadata 逻辑路由，并验证索引 Manifest 与术语版本一致。V1 不创建
`ai_method`、`ai_theory`、`ai_engineering` 三套独立索引。

### Phase 8：持久化版本与复用隔离

每次评审保存 Skill、Classification、Rubric、Retrieval、Terminology、Prompt 版本以及
Resolved Profile 审计快照和 Hash。将 Hash 写入 `review_fingerprint`，验证不同 Skill
不会命中同稿复用。

### Phase 9：建立第二专业

推荐选择法学等与 AI 差异明显的专业。

建立：

```text
law/
├── common/
├── doctrinal/
└── case_study/
```

法学 mandatory rule、证据要求和专业正确性必须由法学人员审核。未经审核的配置只能标记
为 `draft/demo`，不能作为正式法学评审能力对外描述。

### Phase 10：对比评测与正式切换

使用不同专业代表性论文测试：

```text
错误 Skill
vs
正确 Skill
vs
Base / generic Skill
```

重点观察：

```text
专业 Finding 准确性
无关问题误报率
证据可追溯率
Rubric 覆盖率
历史建议相关性
Recall@3 / NDCG@3
评分波动
延迟与失败率
专家关注点是否正确切换
```

AI 无损迁移、错误 Skill 隔离、版本可重建和第二专业对照评测全部通过后，才从 Shadow
切换到正式 Skill 路径。保留旧 AI 适配器作为一个发布周期内的显式回滚路径，而不是
运行时静默回退。

---

## 三十三、最终目标架构

```text
                         Paper
                           ↓
                    Discipline Router
                           ↓
                 Base + Discipline Common
                           ↓
              Paper Type Classification
                           ↓
                  Paper Type Skill
                           ↓
                      Skill Resolver
                           ↓
                 ResolvedReviewProfile
                           ↓
             Chapter Classification / Context
                           ↓
                  Three Specialists
                           ↓
                  Targeted Debate
                           ↓
                         Chair
                           ↓
                  Confirmed Findings
                           ↓
          ┌────────────────┴──────────────────┐
          ↓                                   ↓
   Retrieval Router                    Professional Rubric
          ↓                                   ↓
 Dense + BM25 + RRF + Rerank           Unified 12 Semantic Dims
          ↓                                   ↓
 Professional Suggestions              6 Deterministic Dims
          └────────────────┬──────────────────┘
                           ↓
                 Final Score and Review
```

---

## 三十四、当前结论

V1 将 Skill 正式定位为：

> **由专业与论文类型共同决定的、声明式的评审能力配置包。**

目录采用：

```text
discipline
-> common
-> paper_type
-> skill components
```

而不是将所有论文类型作为跨专业全局 Skill。

第一阶段 RAG 插件化只实现：

```text
Vector DB / Collection 切换
+
BM25 Index 切换
+
Rerank Query Instruction 切换
```

专家采用：

```text
固定 Specialist 槽位
+
固定机器 Role ID
+
不同 Skill 提供不同 Persona、Prompt / Checklist
```

Workflow 最终只依赖统一的：

```text
ResolvedReviewProfile
```

而不直接依赖某个专业目录。

该设计的核心目标不是让 Skill 无限制扩展，而是在保持 Core Engine 稳定、可审计和可测试的前提下，让不同专业的评审知识像插件一样切换。

---

## 三十五、V1.1 实施前验收条件

开始编码前，本方案至少满足以下文档级条件：

```text
固定 Specialist 机器协议与专业 Persona 已分离
专业内论文类型和章节阶段均有明确配置归属
专业 Rubric 能映射到统一 12 个语义维度
6 个确定性维度和总分公式的边界明确
RAG 位于 Chair 确认 Finding 之后
TopK 与当前 RAG V2 实现一致
正式模式 Skill 加载失败时不会静默回退
Resolved Profile Hash 纳入同稿复用隔离
第二专业的专业正确性有人工审核边界
```

本次 V1.1 修订完成了上述架构约束的设计定义。后续实施仍需先产出 Phase 1 的硬编码
迁移矩阵，再进入 Pydantic Schema 和目录建设。
