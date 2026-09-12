# 学术写作与结构规范专家微调 (Qwen3-8B + QLoRA)

基于 `docs/global-quality-finetuning-proposal-v1.md` V1 执行草案。

## 目录结构

```
finetuning/
├── configs/qwen3_8b_qlora_v1.yaml   # 训练配置
├── data/
│   ├── raw/                          # 原始教师标注数据 (*.jsonl)
│   ├── processed/                    # 处理后的 SFT 格式数据
│   └── splits/                       # 训练/验证集划分
├── scripts/
│   ├── prepare_dataset.py            # 数据准备
│   ├── train_qlora.py                # QLoRA 训练
│   ├── evaluate.py                   # 模型评估
│   └── inference.py                  # 交互式推理
├── schemas/finding_schema.json       # ReviewFinding 输出 Schema
├── evaluation/metrics.py             # 评估指标计算
└── outputs/                          # 训练产物
```

## 快速开始

### 1. 安装依赖

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install transformers datasets trl peft accelerate bitsandbytes pyyaml vllm
pip install flash-attn --no-build-isolation
```

### 2. 准备数据

将教师标注的 JSONL 文件放入 `data/raw/`，每条记录格式：

```json
{
  "paper_id": "P001",
  "section": "4.3 实验结果",
  "text": "可以看出来我们的模型还是比较好的。",
  "assistant_output": {
    "status": "valid",
    "findings": [{
      "category": "academic_expression",
      "evidence_quote": "可以看出来我们的模型还是比较好的",
      "severity": "minor",
      "rationale": "表达口语化且缺少具体指标与比较对象",
      "suggestion": "结合具体指标及基线模型说明性能变化"
    }]
  }
}
```

运行数据准备：

```bash
# 处理原始数据并划分训练/验证集
python scripts/prepare_dataset.py \
  --raw-dir data/raw \
  --output data/processed/all.jsonl \
  --splits-dir data/splits \
  --eval-ratio 0.15

# 或者只处理不划分（跳过划分阶段）
python scripts/prepare_dataset.py --skip-split
```

### 3. 训练

```bash
# 使用 GPU 6 训练
python scripts/train_qlora.py --gpu 6

# 干跑验证（不实际训练）
python scripts/train_qlora.py --gpu 6 --dry-run
```

训练参数见 `configs/qwen3_8b_qlora_v1.yaml`。

### 4. 评估

```bash
python scripts/evaluate.py \
  --adapter outputs/global_quality_qwen3_8b_v1/<timestamp>/adapter \
  --test-file data/splits/eval.jsonl \
  --gpu 6 \
  --output evaluation/results.json

# 计算评估指标
python evaluation/metrics.py evaluation/results.json
```

### 5. 交互式推理

```bash
python scripts/inference.py \
  --adapter outputs/global_quality_qwen3_8b_v1/<timestamp>/adapter \
  --gpu 6
```

输入格式: `<论文ID> <章节> <文本>`

## GPU 信息

- 当前可用: GPU 6 (RTX 3090 24GB, 空闲)
- 训练默认使用 `--gpu 6` 指定到此 GPU