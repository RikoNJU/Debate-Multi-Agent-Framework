import json
import argparse
from pathlib import Path

import torch

BASE_DIR = Path(__file__).resolve().parent.parent
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from peft import PeftModel
import yaml


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="Interactive inference with fine-tuned model")
    parser.add_argument("--config", default=str(BASE_DIR / "configs/qwen3_8b_qlora_v1.yaml"))
    parser.add_argument("--adapter", required=True, help="Path to LoRA adapter")
    parser.add_argument("--gpu", type=int, default=6)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.1)
    args = parser.parse_args()

    config = load_config(args.config)
    cfg_base = config["base_model"]
    cfg_quant = config["quantization"]

    model_name = str(BASE_DIR / cfg_base["name"]) if not cfg_base["name"].startswith("/") else cfg_base["name"]

    if args.gpu is not None:
        torch.cuda.set_device(args.gpu)

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
        device_map="auto",
        trust_remote_code=cfg_base["trust_remote_code"],
    )
    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        trust_remote_code=cfg_base["trust_remote_code"],
    )

    print(f"Loading adapter: {args.adapter}")
    model = PeftModel.from_pretrained(model, args.adapter)
    model.eval()

    system_prompt = config["data"]["system_prompt"]

    print("\n=== Qwen3-8B + LoRA 写作与结构规范专家 ===")
    print("输入格式: <论文ID> <章节> <文本>")
    print("输入 'quit' 退出\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input or user_input.lower() == "quit":
            break

        parts = user_input.split(" ", 2)
        if len(parts) < 3:
            print("格式错误。请使用: <论文ID> <章节> <文本>")
            continue

        paper_id, section, text = parts

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"论文ID：{paper_id}\n章节：{section}\n当前文本：{text}"},
        ]

        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                temperature=args.temperature,
                do_sample=True,
                top_p=0.9,
            )

        response = tokenizer.decode(
            outputs[0][len(inputs["input_ids"][0]):], skip_special_tokens=True
        )

        try:
            parsed = json.loads(response)
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
        except json.JSONDecodeError:
            print(response)

        print()


if __name__ == "__main__":
    main()