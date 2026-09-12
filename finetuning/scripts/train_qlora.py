import json
import argparse
import os
import yaml
from pathlib import Path
from datetime import datetime

gpu_id = int(os.environ.get("FINETUNE_GPU", 6))
os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

import torch

BASE_DIR = Path(__file__).resolve().parent.parent
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    BitsAndBytesConfig,
    TrainerCallback,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer, SFTConfig
from datasets import load_dataset


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def format_chat_template(example: dict) -> str:
    tokenizer = format_chat_template.tokenizer
    messages = example["messages"]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=False,
    )


def formatting_func(example: dict) -> str:
    return format_chat_template(example)


def main():
    parser = argparse.ArgumentParser(description="QLoRA fine-tuning for Qwen3-8B")
    parser.add_argument("--config", default=str(BASE_DIR / "configs/qwen3_8b_qlora_v1.yaml"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    cfg_base = config["base_model"]
    cfg_quant = config["quantization"]
    cfg_lora = config["lora"]
    cfg_train = config["training"]
    cfg_data = config["data"]

    model_name = str(BASE_DIR / cfg_base["name"]) if not cfg_base["name"].startswith("/") else cfg_base["name"]
    train_file = str(BASE_DIR / cfg_data["train_file"])
    eval_file = str(BASE_DIR / cfg_data["eval_file"])

    device = torch.cuda.current_device()
    print(f"Using GPU {device}: {torch.cuda.get_device_name(device)}")
    print(f"VRAM: {torch.cuda.get_device_properties(device).total_memory / 1024**3:.1f} GB")

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=cfg_quant["load_in_4bit"],
        bnb_4bit_compute_dtype=getattr(torch, cfg_quant["bnb_4bit_compute_dtype"]),
        bnb_4bit_quant_type=cfg_quant["bnb_4bit_quant_type"],
        bnb_4bit_use_double_quant=cfg_quant["bnb_4bit_use_double_quant"],
    )

    print(f"Loading base model: {model_name}")
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        quantization_config=bnb_config,
        device_map={"": 0},
        trust_remote_code=cfg_base["trust_remote_code"],
    )
    model = prepare_model_for_kbit_training(model)

    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        trust_remote_code=cfg_base["trust_remote_code"],
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    peft_config = LoraConfig(
        r=cfg_lora["r"],
        lora_alpha=cfg_lora["alpha"],
        lora_dropout=cfg_lora["dropout"],
        target_modules=cfg_lora["target_modules"],
        bias=cfg_lora["bias"],
        task_type=cfg_lora["task_type"],
    )

    output_dir = BASE_DIR / cfg_train["output_dir"]
    run_dir = output_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    training_args = SFTConfig(
        output_dir=str(run_dir),
        num_train_epochs=cfg_train["num_train_epochs"],
        per_device_train_batch_size=cfg_train["per_device_train_batch_size"],
        per_device_eval_batch_size=cfg_train["per_device_eval_batch_size"],
        gradient_accumulation_steps=cfg_train["gradient_accumulation_steps"],
        learning_rate=cfg_train["learning_rate"],
        warmup_ratio=cfg_train["warmup_ratio"],
        lr_scheduler_type=cfg_train["lr_scheduler_type"],
        weight_decay=cfg_train["weight_decay"],
        gradient_checkpointing=cfg_train["gradient_checkpointing"],
        gradient_checkpointing_kwargs=cfg_train.get("gradient_checkpointing_kwargs", {}),
        fp16=cfg_train["fp16"],
        bf16=cfg_train["bf16"],
        logging_steps=cfg_train["logging_steps"],
        save_steps=cfg_train["save_steps"],
        eval_steps=cfg_train["eval_steps"],
        eval_strategy="steps",
        save_total_limit=cfg_train["save_total_limit"],
        load_best_model_at_end=cfg_train["load_best_model_at_end"],
        metric_for_best_model=cfg_train["metric_for_best_model"],
        greater_is_better=cfg_train["greater_is_better"],
        dataloader_num_workers=cfg_train["dataloader_num_workers"],
        remove_unused_columns=cfg_train["remove_unused_columns"],
        report_to=cfg_train["report_to"],
        seed=cfg_train["seed"],
        max_length=cfg_train["max_seq_length"],
        logging_dir=str(run_dir / "logs"),
    )

    print(f"Loading dataset from: {train_file}")
    train_dataset = load_dataset("json", data_files=train_file, split="train")
    eval_dataset = load_dataset("json", data_files=eval_file, split="train")

    format_chat_template.tokenizer = tokenizer

    print(f"Train samples: {len(train_dataset)}, Eval samples: {len(eval_dataset)}")

    if args.dry_run:
        print("[DRY RUN] Skipping training. Adapter would be saved to:", run_dir)
        return

    print("Starting QLoRA training...")
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        peft_config=peft_config,
        formatting_func=formatting_func,
    )

    trainer.train()

    final_adapter_dir = run_dir / "adapter"
    trainer.model.save_pretrained(final_adapter_dir)
    tokenizer.save_pretrained(final_adapter_dir)

    print(f"Adapter saved to: {final_adapter_dir}")

    with open(run_dir / "training_summary.json", "w") as f:
        json.dump(
            {
                "config": cfg_base["name"],
                "run_dir": str(run_dir),
                "train_samples": len(train_dataset),
                "eval_samples": len(eval_dataset),
                "timestamp": datetime.now().isoformat(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    main()