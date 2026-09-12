import json
import argparse
from pathlib import Path
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def build_conversational_sample(
    paper_id: str,
    section: str,
    text: str,
    system_prompt: str,
    assistant_output: dict,
) -> dict:
    user_content = text

    if isinstance(assistant_output, str):
        assistant_content = assistant_output
    else:
        assistant_content = json.dumps(assistant_output, ensure_ascii=False)

    return {
        "paper_id": paper_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]
    }


def process_raw_data(raw_dir: str, output_path: str, config: dict) -> None:
    raw_dir = Path(raw_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    system_prompt = config["data"]["system_prompt"]
    samples = []

    for jsonl_file in sorted(raw_dir.glob("*.jsonl")):
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                conv_sample = build_conversational_sample(
                    paper_id=record.get("paper_id", "unknown"),
                    section=record.get("section", "unknown"),
                    text=record.get("text", ""),
                    system_prompt=system_prompt,
                    assistant_output=record.get("assistant_output", {}),
                )
                samples.append(conv_sample)

    with open(output_path, "w") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

    print(f"Processed {len(samples)} samples -> {output_path}")


def split_dataset(input_path: str, splits_dir: str, eval_ratio: float) -> None:
    input_path = Path(input_path)
    splits_dir = Path(splits_dir)
    splits_dir.mkdir(parents=True, exist_ok=True)

    samples = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))

    paper_ids = sorted({s["paper_id"] for s in samples})
    eval_count = max(1, int(len(paper_ids) * eval_ratio))
    eval_pids = set(paper_ids[:eval_count])

    train_samples = []
    eval_samples = []
    for s in samples:
        record = {"messages": s["messages"]}
        if s["paper_id"] in eval_pids:
            eval_samples.append(record)
        else:
            train_samples.append(record)

    train_path = splits_dir / "train.jsonl"
    eval_path = splits_dir / "eval.jsonl"

    with open(train_path, "w") as f:
        for s in train_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    with open(eval_path, "w") as f:
        for s in eval_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    train_paper_count = len(paper_ids) - len(eval_pids)
    print(
        f"Split: {len(train_samples)} train, {len(eval_samples)} eval "
        f"({train_paper_count} / {len(eval_pids)} papers)"
    )


def main():
    parser = argparse.ArgumentParser(description="Prepare fine-tuning dataset")
    parser.add_argument("--config", default=str(BASE_DIR / "configs/qwen3_8b_qlora_v1.yaml"))
    parser.add_argument("--raw-dir", default=str(BASE_DIR / "data/raw"))
    parser.add_argument("--output", default=str(BASE_DIR / "data/processed/all.jsonl"))
    parser.add_argument("--splits-dir", default=str(BASE_DIR / "data/splits"))
    parser.add_argument("--eval-ratio", type=float, default=0.15)
    parser.add_argument("--skip-split", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)

    process_raw_data(args.raw_dir, args.output, config)

    if not args.skip_split:
        split_dataset(args.output, args.splits_dir, args.eval_ratio)


if __name__ == "__main__":
    main()