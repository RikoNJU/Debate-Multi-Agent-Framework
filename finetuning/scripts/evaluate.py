import json
import argparse
from pathlib import Path
from typing import Any

import torch

BASE_DIR = Path(__file__).resolve().parent.parent
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from peft import PeftModel
import yaml


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def parse_finding_schema(schema_path: str) -> dict:
    with open(schema_path) as f:
        return json.load(f)


def build_prompt(sample: dict, system_prompt: str) -> str:
    paper_id = sample.get("paper_id", "unknown")
    section = sample.get("section", "unknown")
    text = sample.get("text", "")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"论文ID：{paper_id}\n章节：{section}\n当前文本：{text}"},
    ]
    return messages


def run_inference(
    model,
    tokenizer,
    messages: list,
    max_new_tokens: int = 1024,
    temperature: float = 0.1,
) -> str:
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=True,
            top_p=0.9,
        )

    response = tokenizer.decode(outputs[0][len(inputs["input_ids"][0]):], skip_special_tokens=True)
    return response


def evaluate_sample(model, tokenizer, sample: dict, system_prompt: str) -> dict:
    messages = build_prompt(sample, system_prompt)
    response = run_inference(model, tokenizer, messages)

    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        parsed = {"status": "parse_error", "raw_output": response}

    return {
        "paper_id": sample.get("paper_id"),
        "section": sample.get("section"),
        "gold": sample.get("assistant_output"),
        "prediction": parsed,
        "raw_prediction": response,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate fine-tuned model")
    parser.add_argument("--config", default=str(BASE_DIR / "configs/qwen3_8b_qlora_v1.yaml"))
    parser.add_argument("--adapter", required=True, help="Path to LoRA adapter")
    parser.add_argument("--test-file", required=True, help="Test JSONL file")
    parser.add_argument("--output", default="evaluation/results.json")
    parser.add_argument("--gpu", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="Limit samples (0=all)")
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

    test_samples = []
    with open(args.test_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            test_samples.append(json.loads(line))

    if args.limit > 0:
        test_samples = test_samples[: args.limit]

    system_prompt = config["data"]["system_prompt"]
    results = []

    for i, sample in enumerate(test_samples):
        print(f"[{i+1}/{len(test_samples)}] Evaluating {sample.get('paper_id', 'unknown')}")
        result = evaluate_sample(model, tokenizer, sample, system_prompt)
        results.append(result)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Results saved to: {output_path}")

    parsed_count = sum(1 for r in results if r["prediction"].get("status") != "parse_error")
    print(f"JSON parse rate: {parsed_count}/{len(results)} ({100*parsed_count/max(1,len(results)):.1f}%)")


if __name__ == "__main__":
    main()