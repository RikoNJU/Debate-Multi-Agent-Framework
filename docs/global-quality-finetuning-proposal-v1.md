# 学术写作与结构规范专家微调方案（V1 执行草案）

## 文档状态

- 状态：执行方案草案，待数据与算力确认后实施
- 原始方案日期：2026-08-21
- 当前方案版本：V1
- 原目标角色：`global_quality`
- 微调后真实角色：`writing_structure_specialist`（学术写作与结构规范专家）
- 基础模型：`Qwen3-8B`
- 微调方式：`QLoRA + SFT`
- 生产策略：离线评估 -> Shadow Mode 旁路运行 -> 教师验收 -> 正式接入
- 明确排除：Chair、最终评分、创新性、技术正确性、实验充分性和纯确定性格式规则

## 背景

当前三个独立评审专家分别为：

```text
scientific_soundness
empirical_evidence
global_quality
```

现有 `global_quality` 的职责过宽，实际上混合承担：

```text
全文结构
+ 工作量
+ 学术表达
+ 规范性
+ 章节关系
+ 贡献完整性
+ 整体质量判断
```

这些任务的确定性、证据范围和难度不同。如果直接用一个 7B/8B 模型替换整个 `global_quality`，会导致任务边界过宽、教师标签难统一、训练数据难构造、评测指标不清晰。

因此，本方案不再把目标定义为“微调一个完整的 global_quality 模型”，而是从其中抽离出最适合领域微调的一部分，形成独立的：

> **学术写作与结构规范专家（Writing & Structure Specialist）**

它只负责论文“写得是否规范、段落和章节是否组织合理、结论是否被当前提供的文本证据支持”等语义审查，不负责科研价值与最终裁决。

---

## 一、此次微调专家的真正功能

此次微调模型的真正功能是：

> **对中文学术论文中的写作质量与结构语义进行可定位、可解释、结构化的专业审查。**

它的核心工作流程是：

```text
发现真实问题
-> 定位证据
-> 判断问题类型
-> 判断严重程度
-> 给出简洁、可执行的修改建议
```

模型的优先级应当是：

```text
少误报
> 证据准确
> 分类稳定
> 建议可执行
> 语言表达漂亮
```

因此模型必须允许：

```text
没有问题
证据不足
解析不可靠
需要人工复核
```

而不是见到论文就强行产生建议。

### V1 负责的 8 类问题

```text
academic_expression
ambiguity
redundancy
logical_jump
section_role
abstract_completeness
terminology_consistency
evidence_conclusion_mismatch
```

具体含义：

- `academic_expression`：学术语言不够规范、正式、客观；
- `ambiguity`：指代不清、描述模糊、缺少比较对象或明确结论；
- `redundancy`：重复论述、内容冗余和不必要展开；
- `logical_jump`：段落、论证或章节之间存在明显逻辑跳跃；
- `section_role`：当前章节内容没有履行该章节应有职责；
- `abstract_completeness`：摘要没有完整覆盖问题、方法、结果和结论；
- `terminology_consistency`：术语、缩写、符号使用前后不一致；
- `evidence_conclusion_mismatch`：结论明显超出当前提供的正文证据。

---

## 二、为什么要把原 `global_quality` 拆开

### 第一层：确定性质量检查

以下内容不交给微调模型：

- 是否存在中文摘要、英文摘要、目录、正文、参考文献和致谢；
- 摘要字数和关键词数量；
- 章节标题层级和编号连续性；
- 图、表、公式是否有编号和可定位位置；
- 正文字数和章节数量；
- 参考文献是否包含基础著录元素；
- 致谢长度；
- 页码、坐标、Block ID 和 Chunk ID；
- 论文类型对应的基础工作量阈值。

这些任务共同特点是：

> **答案主要来自客观事实，不需要复杂语言推理。**

因此由：

```text
MinerU
+
Canonical Paper Record
+
确定性规则
```

完成。

这样划分的原因：

1. 程序判断比生成模型稳定；
2. 可解释、可复现；
3. 几乎没有幻觉；
4. 成本低；
5. 规则变化时修改代码即可，不需要重新训练。

### 第二层：学术写作与结构语义

以下任务交给此次 Qwen3-8B + QLoRA：

- 学术语言是否规范、准确和客观；
- 是否存在口语化、模糊化或缺乏指代的表达；
- 段落中心是否清晰；
- 是否存在重复论述；
- 是否存在逻辑跳跃；
- 术语、缩写和符号是否前后一致；
- 当前章节是否履行应有职责；
- 前后章节是否形成合理递进；
- 摘要是否包含问题、方法、结果、结论；
- 引言是否形成清晰研究问题和研究动机；
- 方法、实验、结果、结论之间是否形成基本对应；
- 结论是否超出当前正文证据；
- 修改建议是否保持原意并符合学术表达习惯。

这些任务共同特点是：

> **需要语言理解和上下文判断，但通常不需要完整科研价值裁决。**

这是此次领域微调真正要学习的能力。

### 第三层：科研质量与最终裁决

以下内容明确不作为此次微调目标：

- 创新性是否成立；
- 科研贡献是否足够；
- 方法是否技术正确；
- 理论是否成立；
- 实验是否充分；
- 结果是否具有显著性；
- 论文整体研究价值；
- 是否达到毕业要求；
- 最终评分和是否通过。

这些职责继续由：

```text
scientific_soundness
empirical_evidence
Chair
```

承担。

原因是这些判断依赖跨全文证据、教师主观差异更大，也更容易让 8B 小模型产生“语言很专业但科研判断错误”的假专业现象。

---

## 三、职责拆分后的系统结构

```text
MinerU
    ↓
Canonical Paper Record
    ↓
┌──────────────────────────┬──────────────────────────┐
↓                          ↓
Deterministic Checker      Semantic Input Builder
客观格式/结构事实           局部正文 + 全局结构摘要
↓                          ↓
Format Findings            Qwen3-8B + QLoRA
                            ↓
                       Semantic Findings
└───────────────┬──────────┘
                ↓
        Finding Validator
                ↓
        Evidence Validator
                ↓
      Global Quality Aggregator
                ↓
  IndependentReview / ReviewFinding
                ↓
              Chair
```

微调模型输出不能未经校验直接进入 Chair。

---

## 四、模型方案

V1 正式采用：

```text
Base Model:
Qwen3-8B

Fine-tuning:
QLoRA + SFT
```

选择原因：

1. 中文能力适合中文学术论文；
2. 8B 容量足以承担语义结构判断；
3. 直接使用 8B 可减少“到底是模型太小还是任务设计有问题”的不确定性；
4. LoRA/QLoRA 工具链成熟；
5. 可以通过 4-bit 量化降低训练显存；
6. 适合云端训练、本地保存 Adapter；
7. 可独立部署，降低对外部 API 的依赖。

第一阶段不同时训练 1.5B、3B 和 8B。

本阶段真正要验证的是：

> **领域 QLoRA 是否能让 Qwen3-8B 稳定学习中文学术写作与结构审查任务。**

---

## 五、训练输入设计

不把整篇论文直接作为一个训练样本。

### 5.1 段落级输入

```text
论文类型
当前章节
章节阶段
当前段落
必要前后文
```

主要用于：

```text
academic_expression
ambiguity
redundancy
logical_jump
terminology_consistency
```

### 5.2 小节 / 章节级输入

```text
论文类型
全文目录
当前章节标题
当前章节摘要或压缩文本
前一节和后一节标题
必要上下文
```

主要用于：

```text
section_role
章节递进
重复内容
结构不完整
```

### 5.3 全局结构输入

不直接输入全文，而是先生成：

```text
Paper Structural Digest
```

例如：

```json
{
  "title": "...",
  "abstract_summary": "...",
  "research_problem": "...",
  "method_summary": "...",
  "experiment_summary": "...",
  "main_results": "...",
  "conclusion_summary": "...",
  "section_outline": []
}
```

再用于：

```text
abstract_completeness
evidence_conclusion_mismatch
跨章节基本对应关系
```

---

## 六、输出设计

正式实现复用现有：

```text
ReviewFinding
IndependentReview
```

概念输出：

```json
{
  "status": "valid",
  "summary": "本节结构与表达评价",
  "findings": [
    {
      "category": "academic_expression",
      "claim": "本段表达口语化且缺少明确实验指标",
      "evidence_quote": "可以看出来我们的模型还是比较好的",
      "severity": "minor",
      "rationale": "缺少具体指标、比较对象和明确结论",
      "suggestion": "结合具体评价指标说明相对基线的性能变化",
      "confidence": 0.91
    }
  ]
}
```

允许无问题：

```json
{
  "status": "valid",
  "findings": []
}
```

允许证据不足：

```json
{
  "status": "insufficient_evidence",
  "findings": []
}
```

建议一级状态：

```text
valid
insufficient_context
parser_uncertain
manual_review_required
```

---

## 七、教师数据要求

优先级：

```text
教师修改前后文本对
>
带教师批注的论文段落
>
教师明确指出的结构问题
>
教师确认没有问题的正常文本
>
教师确认的难负例
>
教师争议样本
```

每条语义样本至少保留：

```text
paper_id
paper_type
chapter_stage
issue_category
evidence_quote
teacher_judgment
severity
rationale
recommended_revision
annotator_id
adjudication_status
```

高风险或争议样本建议双标，意见不一致时进行裁决。

---

## 八、数据规模

### 阶段 0：工程验证

```text
约 50 条
```

验证：

- 数据格式；
- 训练脚本；
- 显存；
- Chat Template；
- Adapter 保存与加载；
- JSON 输出。

不评价最终效果。

### 阶段 1：任务可学习性验证

```text
500～1000 条
```

验证：

> Qwen3-8B + QLoRA 是否明显优于原始 Qwen3-8B。

若没有提升，暂停扩数据，先检查：

```text
任务边界
标签一致性
Prompt
样本质量
问题类别定义
```

### 阶段 2：正式 V1

```text
3000～5000 条
```

其中必须包含足够的：

```text
正常样本
难负例
章节结构问题
争议 / 裁决样本
不同论文类型
不同研究方向
```

---


## 八-A、400 条真实教师建议条件下的数据构建方案

如果当前能够获得的高质量真实教师建议约为 400 条，不建议因为总量不足而停止实验，也不建议为了“凑够 3000 条”而大量加入低质量合成数据。

V1 应将这 400 条真实教师建议视为：

> **Gold Data（最高可信监督信号）**

它们的主要作用是确定：

```text
什么问题值得报
问题应该归到哪一类
证据应该如何定位
教师认为问题为什么成立
什么情况不应该误报
```

### 8-A.1 真实教师数据的核心形式

当前即使只有：

```text
学生原稿
+
教师修改建议
```

而没有教师直接修改后的完整正文，也可以用于训练。

例如：

```text
学生原文：
通过实验可以看出，我们的方法还是比较好的。

教师建议：
表述不够严谨，应结合具体指标和对比对象说明实验结果。
```

可以整理为：

```json
{
  "source_type": "teacher_gold",
  "has_issue": true,
  "category": "academic_expression",
  "evidence_quote": "我们的方法还是比较好的",
  "rationale": "结论缺少具体指标与比较对象，表达不够严谨",
  "suggestion": "结合具体评价指标和基线模型说明性能差异"
}
```

因此，教师没有给出完整修改稿并不会阻止第一阶段微调。

V1 的主要学习目标仍然是：

```text
原始文本
-> 识别是否有问题
-> 问题分类
-> 证据定位
-> 解释教师为什么认为有问题
```

而不是要求模型逐字复现教师修改后的最终正文。

### 8-A.2 推荐数据构成

假设现有真实教师建议约 400 条，可将训练数据池设计为：

| 数据类型 | 参考数量 | 主要作用 |
|---|---:|---|
| 真实教师问题数据 | 400 | 核心 Gold supervision |
| 高质量中文正常片段 | 300 | 教模型什么时候不应报错 |
| 中文顶刊/高水平中文期刊正常片段 | 200 | 补充高质量结构与论证样本 |
| 受控合成问题 | 500 | 扩充问题覆盖和类别平衡 |
| 难负例 | 200 | 降低机械误报 |
| **总计** | **约 1600** | V1 可学习性验证数据池 |

这里的数量不是固定指标，真正原则是：

```text
真实教师数据决定任务标准
正常数据控制误报
受控合成数据补覆盖
难负例训练判断边界
```

不允许让合成数据数量远远压过真实 Gold Data，使模型主要学习人工构造模式。

### 8-A.3 高质量正常数据

正常数据用于训练：

```text
status = valid
findings = []
```

即让模型学会：

> 文本没有明显问题时，应当不提出问题。

优先来源：

```text
教师确认写得好的中文论文片段
中文顶刊或高水平中文期刊论文
优秀中文硕博论文
优秀本科毕业论文
其他有明确授权的高质量中文学术文本
```

中文顶刊样本建议优先选择与实际评审对象相近的学科、论文类型和章节类型，避免全部来自与本科/硕士论文差异过大的文章体例。

高质量正常数据非常重要，因为训练集若大部分都是“有问题文本”，模型容易形成：

```text
看到论文
-> 默认必须找出若干问题
```

最终导致正式评审误报率过高。

### 8-A.4 中文顶刊/高水平中文期刊数据的使用方式

中文顶刊和高水平中文期刊论文可以作为高质量正常参考数据，但不应简单定义为：

```text
中文顶刊 = 好
学生论文 = 坏
```

否则模型可能学习错误的 Dataset Shortcut，例如：

```text
顶刊写作风格 = 好
复杂句 = 好
学生写作风格 = 差
```

对于中文顶刊和高水平中文期刊论文，可以在符合数据许可要求的前提下，直接抽取经过筛选的高质量中文片段作为：

```text
Clean Reference
+
Structural Reference
```

这类中文高质量论文数据主要用于：

```text
摘要结构
研究问题—方法—实验—结果—结论对应关系
章节职责
论证结构
证据与结论关系
```

由于这些数据本身就是中文学术文本，因此可同时用于补充：

```text
中文学术表达规范
章节组织
摘要结构
论证结构
术语使用
证据与结论关系
```

但它们仍然只能作为“高质量正常参考”，不能替代教师 Gold Data。真正决定“什么问题值得报”的标准，仍然由真实教师反馈定义。

### 8-A.5 受控合成数据

教师 Gold Data 不足时，可以通过“受控扰动正常文本”的方式增加训练样本。

原则不是让大模型自由生成：

```text
请生成 1000 条有问题的论文段落
```

而是：

> 从真实正常文本出发，人工或程序化制造已知错误。

例如正常文本：

```text
实验结果表明，本方法在 F1 指标上达到 91.3%，
较 BERT 基线提高 2.7 个百分点。
```

可受控修改为：

```text
实验结果表明，本方法取得了比较好的效果。
```

对应标签：

```text
academic_expression
```

也可以删除具体证据：

```text
本方法显著优于现有方法。
```

对应：

```text
evidence_alignment
```

还可以构造：

```text
重复论述
逻辑衔接断裂
术语不一致
摘要要素缺失
章节职责错位
```

这种合成数据的优势在于：

```text
原始正确文本已知
错误位置已知
错误类型已知
修改方向已知
```

因此比“自由生成坏论文”更可靠。

### 8-A.6 难负例

难负例是看起来“像问题”，但结合上下文其实合理的文本。

例如：

```text
实验结果表明，本方法取得了较好的性能。
```

单独看可能被认为“较好”过于模糊。

但如果后文立即给出：

```text
准确率达到 94.7%，较基线提高 4.3 个百分点。
```

那么该表达在完整上下文中未必需要报错。

这类数据用于训练模型：

```text
不要看到某个关键词就机械触发规则
```

难负例对降低正式系统误报率尤其重要。

### 8-A.7 教师建议的辅助标注

真实教师建议可能非常简短，例如：

```text
这里逻辑不清楚
表述不规范
需要重写
```

可以使用强模型进行预标注：

```text
学生原文
+
教师原始建议
-> 预生成
category
evidence_quote
rationale
severity
suggestion
```

但预标注结果不能直接视为 Gold Label。

必须经过人工快速审核：

```text
分类是否忠实于教师原意
证据是否来自原文
是否凭空扩展教师意见
建议是否合理
```

该流程属于：

```text
assisted annotation / weak supervision
```

目的是降低人工标注成本，而不是用通用大模型替代教师监督。

### 8-A.8 真实教师数据的划分

真实教师数据是最珍贵的数据，因此不能全部用于训练。

建议在约 400 条真实教师数据中保留一部分作为冻结测试集。

示意：

```text
真实教师 Gold Data：400 条

约 280～300 条
-> train

约 40～60 条
-> validation

约 60～80 条
-> frozen test
```

实际划分必须继续遵守：

```text
按 paper_id 划分
```

而不是按段落随机划分。

即：

```text
一篇论文的所有片段只能存在于一个数据分区
```

### 8-A.9 测试集原则

最终测试集应尽量只包含：

```text
真实学生论文
+
真实教师意见
```

测试集不应主要由：

```text
中文顶刊参考数据
合成问题
人工扰动数据
```

组成。

否则最终只能证明：

```text
模型能识别人工制造的问题
```

而不能证明：

```text
模型能够逼近真实教师判断
```

因此：

```text
训练集
= Gold + Clean + Synthetic + Hard Negative

冻结测试集
≈ 真实 Teacher Gold
```

### 8-A.10 400 条条件下的 V1 目标

在真实教师建议只有约 400 条时，V1 不应该直接宣称：

```text
构建生产级学术评审模型
```

更合理的目标是：

> 验证在有限教师监督数据下，Qwen3-8B 经 QLoRA 后，是否能比原始 Qwen3-8B 更稳定地识别教师关注的学术写作与结构问题。

V1 成功标准首先是：

```text
Qwen3-8B + QLoRA
>
Qwen3-8B Base
```

并在以下核心指标上具有稳定提升：

```text
Finding Precision
Finding Recall
Finding F1
Clean Sample Pass Rate
Evidence Accuracy
```

若 400 条真实数据条件下已经出现明确提升，再继续争取更多教师数据并扩大问题类别。

### 8-A.11 推荐执行顺序

```text
400 条真实教师建议
        ↓
统一 category 与标注 Schema
        ↓
人工 / 强模型辅助完成结构化标注
        ↓
按 paper_id 划分 Gold train / val / test
        ↓
补充约 300 条高质量中文正常数据
        ↓
补充约 200 条中文顶刊/高水平中文期刊正常参考片段
        ↓
基于正常文本制造约 500 条受控合成问题
        ↓
准备约 200 条难负例
        ↓
形成约 1600 条 V1 数据池
        ↓
跑 Qwen3-8B Base
        ↓
跑 Qwen3-8B + QLoRA
        ↓
仅在真实 Teacher Gold 冻结测试集上做核心比较
```


## 九、数据划分

训练集、验证集、测试集必须按论文划分：

```text
paper_id
-> train / validation / test
```

推荐：

```text
70% train
15% validation
15% test
```

一篇论文的全部段落只能出现在一个分区。

测试集冻结后，不得继续用于训练和 Prompt 调优。

---

## 十、SFT 数据格式

进入 SFT Trainer 前，转换为 conversational dataset：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是中文学术写作与结构规范评审专家。只依据提供文本和结构证据判断。证据不足时不得强行提出问题。严格输出指定 JSON。"
    },
    {
      "role": "user",
      "content": "论文类型：本科毕业论文\\n章节：4.3 实验结果\\n当前文本：可以看出来我们的模型还是比较好的。"
    },
    {
      "role": "assistant",
      "content": "{\"status\":\"valid\",\"findings\":[{\"category\":\"academic_expression\",\"evidence_quote\":\"可以看出来我们的模型还是比较好的\",\"severity\":\"minor\",\"rationale\":\"表达口语化且缺少具体指标与比较对象\",\"suggestion\":\"结合具体指标及基线模型说明性能变化\"}]}"
    }
  ]
}
```

---

## 十一、训练方式

V1：

```text
Qwen3-8B
+
4-bit 量化
+
QLoRA
+
SFT
```

工具链：

```text
PyTorch
Transformers
Datasets
TRL
PEFT
bitsandbytes
```

第一阶段不采用：

```text
全参数微调
DPO
PPO
GRPO
RLHF
多机多卡
DeepSpeed ZeRO-3
```

---

## 十二、V1 推荐训练参数

第一轮仅作为 baseline：

```text
Base Model:
Qwen3-8B

Quantization:
4-bit NF4
double_quantization = true
compute_dtype = bfloat16

LoRA:
r = 16
alpha = 32
dropout = 0.05

Training:
epochs = 2~3
learning_rate = 1e-4
batch_size = 1
gradient_accumulation_steps = 8
max_length = 4096
warmup_ratio = 0.05
gradient_checkpointing = true
```

第一版 LoRA 优先挂：

```text
q_proj
k_proj
v_proj
o_proj
```

效果不足时，再评估：

```text
gate_proj
up_proj
down_proj
```

以上参数只是第一轮实验起点，不视为最终最优配置。

---

## 十三、算力与产物

第一次实验建议：

```text
RTX 4090 24GB
```

或更高显存：

```text
A6000 48GB
L40S 48GB
A100 40GB / 80GB
```

训练在租用 GPU 上完成。

核心训练产物：

```text
adapter_model.safetensors
adapter_config.json
tokenizer/config
训练参数
数据版本
Prompt 版本
评测版本
```

训练结束后只需要把 LoRA Adapter 下载回本地部署环境。

运行时：

```text
Qwen3-8B Base
+
global_quality_adapter_v1
```

---

## 十四、完整微调执行流程

### Phase 0：定义任务

```text
固定 8 个 issue category
-> 对齐 ReviewFinding Schema
-> 定义无问题
-> 定义证据不足
```

### Phase 1：准备数据

```text
收集教师数据
-> 确认授权
-> 脱敏
-> Canonical Record
-> 标注
-> 双标 / 裁决争议样本
-> 按 paper_id 划分
```

### Phase 2：建立基线

必须先运行：

```text
A：当前 DeepSeek global_quality
B：原始 Qwen3-8B
```

使用同一冻结测试集。

### Phase 3：50 条工程验证

```text
Qwen3-8B + QLoRA + 50 条
```

只检查：

```text
训练能否启动
是否 OOM
Loss 是否正常
Checkpoint 是否保存
Adapter 是否能重新加载
JSON 是否可解析
```

### Phase 4：500～1000 条任务验证

核心比较：

```text
Qwen3-8B Base
vs
Qwen3-8B + QLoRA
```

如果没有明显提升：

```text
停止扩大训练
-> 检查数据
-> 检查标签
-> 检查 Prompt
-> 检查任务定义
```

### Phase 5：3000～5000 条正式训练

在任务可学后扩充：

```text
正常样本
难负例
章节级样本
不同论文类型
不同研究方向
教师裁决样本
```

仅做少量关键参数实验，不做盲目大规模扫参。

### Phase 6：冻结测试集评估

最终比较：

```text
A：DeepSeek global_quality
B：Qwen3-8B
C：Qwen3-8B + QLoRA
```

### Phase 7：人工盲评

教师不知道模型来源，只判断：

```text
问题是否成立
证据是否支持结论
严重程度是否合理
建议是否可执行
是否存在误报
```

### Phase 8：本地推理验证

```text
下载 LoRA Adapter
-> 本地加载 Qwen3-8B Base
-> Transformers + PEFT 推理
-> 验证结果一致性
```

### Phase 9：模型服务化

```text
Qwen3-8B + Adapter
-> vLLM
-> OpenAI-compatible API
-> FastAPI / Workflow Service
```

### Phase 10：LangGraph Shadow Mode

先旁路，不影响正式报告：

```text
正式 global_quality
-> DeepSeek

旁路 global_quality
-> Qwen3-8B + QLoRA
```

同时记录两套结果。

### Phase 11：正式接入

达到验收条件后：

```text
scientific_soundness -> DeepSeek
empirical_evidence   -> DeepSeek
global_quality       -> Qwen3-8B + global_quality_adapter_v1
chair                -> DeepSeek
```

---

## 十五、评估指标

| 指标 | 目的 |
|---|---|
| Finding Precision | 模型指出的问题中多少真实成立 |
| Finding Recall | 教师问题被发现多少 |
| Finding F1 | 综合问题检测能力 |
| Clean Sample Pass Rate | 正常文本能否不强行挑错 |
| 正常样本误报率 | 衡量系统性误报 |
| Evidence Accuracy | 证据引用是否准确 |
| Evidence Entailment Rate | 引用是否真正支持问题结论 |
| Severity Accuracy | 严重程度是否合理 |
| Suggestion Actionability | 建议是否可执行 |
| Schema Success Rate | 是否稳定进入工作流 |
| Cross-paper-type Performance | 是否过拟合某类论文 |
| Latency | 推理延迟 |
| GPU Memory | 部署显存 |
| Cost per Paper | 单篇成本 |

---

## 十六、模型路由

建议：

```text
role
-> model_provider
-> model_name
-> adapter_version
```

示意：

```yaml
scientific_soundness:
  provider: deepseek
  model: DeepSeek-V4-Pro

empirical_evidence:
  provider: deepseek
  model: DeepSeek-V4-Pro

global_quality:
  provider: local_vllm
  model: Qwen3-8B
  adapter: global_quality-v1

chair:
  provider: deepseek
  model: DeepSeek-V4-Pro
```

---

## 十七、回退策略

```text
Qwen3-8B + LoRA 正常
-> 使用本地模型结果

模型超时 / 服务不可用 / Schema 校验失败
-> 回退 DeepSeek global_quality

DeepSeek 仍失败
-> 按现有最少独立评审数量规则降级
```

必须记录：

```text
requested_model
actual_model
adapter_version
fallback_reason
latency
schema_validation_result
```

---

## 十八、上线门槛

只有满足以下条件，才允许从旁路进入正式工作流：

- Qwen3-8B + QLoRA 在冻结测试集上明显优于 Qwen3-8B Base；
- 相比当前 DeepSeek 路径，在质量、成本或延迟中至少有一项明确优势；
- 正常样本误报率达到教师可接受水平；
- Evidence Accuracy 和 Evidence Entailment 达标；
- Schema 输出稳定；
- 不会把 MinerU 解析缺失误判为论文问题；
- 支持超时、异常和模型不可用回退；
- Base Model、Adapter、Prompt、数据和评测版本可追溯。

具体数值阈值在获得真实教师基线后确定。

---

## 十九、与专业 Skill 的关系

```text
专业 Skill
-> 保存专业术语、领域规则、证据标准和易变化规范

LoRA Adapter
-> 学习稳定学术表达
-> 学习结构判断习惯
-> 学习问题分类
-> 学习证据约束
-> 学习结构化输出
```

未来可以发展：

```text
Qwen3-8B Base
├── AI Adapter
├── Finance Adapter
└── Other Adapter
```

但 V1 只验证一个专业 Adapter。

---

## 二十、推荐项目目录

```text
finetuning/
├── README.md
├── configs/
│   └── qwen3_8b_qlora_v1.yaml
├── data/
│   ├── raw/
│   ├── processed/
│   ├── splits/
│   └── README.md
├── scripts/
│   ├── prepare_dataset.py
│   ├── train_qlora.py
│   ├── evaluate.py
│   └── inference.py
├── schemas/
│   └── finding_schema.json
├── evaluation/
│   ├── metrics.py
│   └── blind_review.csv
└── outputs/
    └── global_quality_qwen3_8b_v1/
```

真实论文、教师敏感标注和模型大权重不得提交到 Git。

---

## 二十一、V1 技术栈

```text
Document Parsing:
MinerU

Canonical Data:
Canonical Paper Record

Base Model:
Qwen3-8B

Fine-tuning:
QLoRA + SFT

Training:
PyTorch
Transformers
Datasets
TRL
PEFT
bitsandbytes

Training GPU:
RTX 4090 24GB 或更高

Serving:
vLLM

Backend:
FastAPI

Workflow:
LangGraph

Fallback:
DeepSeek-V4-Pro

Output:
ReviewFinding / IndependentReview
```

---

## 二十二、最终目标

此次微调项目不是为了“训练一个更小的 DeepSeek 替代品”，而是为了证明：

> **通过明确角色拆分、教师监督数据、QLoRA 领域微调、确定性规则、证据校验和模型路由，可以让一个 8B 级本地模型稳定承担“学术写作与结构规范”这一清晰子任务。**

最终系统形成：

```text
确定性规则
+
领域微调模型
+
通用大模型专家
+
Chair 裁决
```

的异构多智能体评审架构。

---

## 二十三、下一步执行清单

1. 固定 8 个 `issue_category`。
2. 对齐现有 `ReviewFinding` / `IndependentReview` Schema。
3. 设计训练样本 JSON Schema。
4. 收集第一批教师数据。
5. 制作约 50 条可训练样本。
6. 完成匿名化和 paper_id 级数据划分。
7. 跑 Qwen3-8B 原始基线。
8. 租用 4090 / 48GB GPU。
9. 跑 50 条 QLoRA 工程验证。
10. 扩到 500～1000 条验证任务可学习性。
11. 任务有效后扩到 3000～5000 条。
12. 完成冻结测试集评估和教师盲评。
13. 下载 Adapter 并本地推理验证。
14. 使用 vLLM 服务化。
15. 接入 LangGraph Shadow Mode。
16. 达标后正式替换 `global_quality` 的语义子任务。
