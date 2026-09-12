import json
import os
import threading
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

from backend.env import ChatMessage, ModelCallOptions, ModelClient, ModelResponse

_ADAPTER_PATH = os.getenv(
    "QWEN_ADAPTER_PATH",
    str(Path(__file__).resolve().parent.parent.parent.parent.parent
        / "finetuning/outputs/global_quality_qwen3_8b_v1"
        / "20260828_050412/adapter"),
)
_BASE_MODEL_PATH = os.getenv(
    "QWEN_BASE_MODEL",
    str(Path(__file__).resolve().parent.parent.parent.parent.parent
        / "finetuning/models/Qwen3-8B/Qwen/Qwen3-8B"),
)
_SYSTEM_PROMPT = os.getenv(
    "QWEN_SYSTEM_PROMPT",
    "你是中文学术写作与结构规范评审专家。只依据提供文本和结构证据判断。证据不足时不得强行提出问题。严格输出指定 JSON。",
)
_GPU_ID = int(os.getenv("QWEN_GPU_ID", "6"))


class QwenLocalClient:

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            device = torch.device(f"cuda:{_GPU_ID}")
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
            print(f"[QwenLocal] Loading base model from {_BASE_MODEL_PATH} on cuda:{_GPU_ID}")
            model = AutoModelForCausalLM.from_pretrained(
                _BASE_MODEL_PATH,
                quantization_config=bnb_config,
                device_map={"": device},
                trust_remote_code=True,
            )
            tokenizer = AutoTokenizer.from_pretrained(
                _BASE_MODEL_PATH,
                trust_remote_code=True,
            )
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
            print(f"[QwenLocal] Loading adapter from {_ADAPTER_PATH}")
            model = PeftModel.from_pretrained(model, _ADAPTER_PATH)
            model.eval()
            self._model = model
            self._tokenizer = tokenizer
            self._loaded = True
            print("[QwenLocal] Model loaded successfully")

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        self._ensure_loaded()
        with self._lock:
            msgs: list[dict[str, str]] = []
            for message in messages:
                msgs.append({"role": message.role, "content": message.content})
            if not msgs or msgs[0]["role"] != "system":
                msgs.insert(0, {"role": "system", "content": _SYSTEM_PROMPT})
            prompt = self._tokenizer.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True
            )
            opts = options or ModelCallOptions()
            temperature = opts.temperature if opts.temperature is not None else 0.1
            max_tokens = opts.max_tokens if opts.max_tokens is not None else 4096
            inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
            with torch.no_grad():
                outputs = self._model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    do_sample=temperature > 0,
                    top_p=0.9,
                    pad_token_id=self._tokenizer.pad_token_id,
                )
            generated_ids = outputs[0][len(inputs["input_ids"][0]):]
            response_ids = generated_ids.tolist()
            response_part = self._tokenizer.decode(
                generated_ids, skip_special_tokens=True
            )
            for i in range(len(response_ids) - 1):
                if response_ids[i] == 151668 and response_ids[i + 1] == 271:
                    after = response_ids[i + 2:]
                    after_tensor = torch.tensor([after], device=generated_ids.device)
                    response_part = self._tokenizer.decode(
                        after_tensor[0], skip_special_tokens=True
                    )
                    break
            response_part = response_part.strip()
            gen_len = outputs[0].shape[0] - inputs["input_ids"][0].shape[0]
            finish_reason = "length" if gen_len >= max_tokens else "stop"
            return ModelResponse(
                content=response_part,
                raw={"finish_reason": finish_reason},
                usage={},
            )

    async def acomplete(
        self,
        messages: list[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        import asyncio

        return await asyncio.to_thread(self.complete, messages, options=options)