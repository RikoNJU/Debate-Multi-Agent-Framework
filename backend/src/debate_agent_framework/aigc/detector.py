from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Sequence


class AigcDetectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class AigcDetectorConfig:
    enabled: bool = False
    model_id: str = "yuchuantian/AIGC_detector_zhv3"
    model_revision: str | None = None
    device: str = "auto"
    batch_size: int = 8
    ai_label_index: int = 1
    local_files_only: bool = False
    medium_threshold: float = 0.5
    high_threshold: float = 0.8

    @classmethod
    def from_env(cls) -> "AigcDetectorConfig":
        return cls(
            enabled=os.getenv("DEBATE_AIGC_ENABLED", "false").lower() == "true",
            model_id=os.getenv(
                "DEBATE_AIGC_MODEL_ID",
                os.getenv(
                    "DEBATE_AIGC_MODEL_PATH", "yuchuantian/AIGC_detector_zhv3"
                ),
            ),
            model_revision=os.getenv("DEBATE_AIGC_MODEL_REVISION") or None,
            device=os.getenv("DEBATE_AIGC_DEVICE", "auto"),
            batch_size=int(os.getenv("DEBATE_AIGC_BATCH_SIZE", "8")),
            ai_label_index=int(os.getenv("DEBATE_AIGC_AI_LABEL_INDEX", "1")),
            local_files_only=os.getenv(
                "DEBATE_AIGC_LOCAL_FILES_ONLY", "false"
            ).lower()
            == "true",
            medium_threshold=float(os.getenv("DEBATE_AIGC_MEDIUM_THRESHOLD", "0.5")),
            high_threshold=float(os.getenv("DEBATE_AIGC_HIGH_THRESHOLD", "0.8")),
        )

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("DEBATE_AIGC_BATCH_SIZE must be positive")
        if not 0 <= self.medium_threshold < self.high_threshold <= 1:
            raise ValueError("AIGC risk thresholds are invalid")


class HuggingFaceAigcDetector:
    """Lazy local sequence classifier; paper text never leaves this process."""

    def __init__(self, config: AigcDetectorConfig) -> None:
        self.config = config
        self._load_lock = Lock()
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._torch: Any | None = None
        self._device: Any | None = None

    @property
    def dependencies_installed(self) -> bool:
        return all(
            importlib.util.find_spec(name) is not None
            for name in ("torch", "transformers")
        )

    def encode(self, text: str) -> list[int]:
        self._ensure_loaded()
        return list(self._tokenizer.encode(text, add_special_tokens=False))

    def decode(self, token_ids: Sequence[int]) -> str:
        self._ensure_loaded()
        return str(
            self._tokenizer.decode(
                list(token_ids), skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
        )

    def predict_probabilities(self, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        self._ensure_loaded()
        probabilities: list[float] = []
        for offset in range(0, len(texts), self.config.batch_size):
            batch = list(texts[offset : offset + self.config.batch_size])
            inputs = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            inputs = {key: value.to(self._device) for key, value in inputs.items()}
            with self._torch.inference_mode():
                logits = self._model(**inputs).logits
                scores = self._torch.softmax(logits, dim=-1)
            probabilities.extend(
                float(value)
                for value in scores[:, self.config.ai_label_index].detach().cpu()
            )
        if len(probabilities) != len(texts):
            raise AigcDetectorError("AIGC model returned an incomplete batch")
        return probabilities

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if not self.config.enabled:
            raise AigcDetectorError("AIGC detection is disabled")
        if not self.dependencies_installed:
            raise AigcDetectorError(
                "AIGC dependencies are missing; install the project with the 'aigc' extra"
            )
        with self._load_lock:
            if self._model is not None:
                return
            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                source = self.config.model_id
                if Path(source).exists():
                    source = str(Path(source).resolve())
                kwargs = {
                    "revision": self.config.model_revision,
                    "local_files_only": self.config.local_files_only,
                }
                tokenizer = AutoTokenizer.from_pretrained(source, **kwargs)
                model = AutoModelForSequenceClassification.from_pretrained(source, **kwargs)
                if int(getattr(model.config, "num_labels", 0)) != 2:
                    raise AigcDetectorError("AIGC model must expose exactly two labels")
                if self.config.ai_label_index not in (0, 1):
                    raise AigcDetectorError("AIGC AI label index must be 0 or 1")
                device = self.config.device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                model.to(device)
                model.eval()
                self._torch = torch
                self._tokenizer = tokenizer
                self._model = model
                self._device = device
            except AigcDetectorError:
                raise
            except Exception as exc:
                raise AigcDetectorError(f"Failed to load AIGC model: {exc}") from exc
