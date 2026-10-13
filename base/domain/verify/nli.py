"""The local NLI verifier: a multilingual model run in-process on the CPU.

Model: `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`. It is
multilingual (the wikis are in Spanish and English), trained on XNLI and a
multilingual NLI set, about 280 million parameters, and the one the design note
names. Its input limit is 512 tokens; a premise of 1200 characters is about 300
tokens in Spanish, which leaves room for the sentence.

`transformers` and `torch` are optional (group `verify`), so they are imported
inside the functions. `is_available` tells the web interface whether to offer
the verification.
"""

from __future__ import annotations

import importlib.util
import threading
from collections.abc import Sequence

MODEL_NAME = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
MAX_PASSAGE_CHARS = 1200
BATCH_SIZE = 8
_MAX_TOKENS = 512


def is_installed() -> bool:
    """True if `transformers` and `torch` can be imported."""
    return all(importlib.util.find_spec(m) is not None for m in ("transformers", "torch"))


def model_is_cached(model_name: str = MODEL_NAME) -> bool:
    """True if the model files are in the local Hugging Face cache."""
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(model_name, local_files_only=True)
        return True
    except Exception:
        return False


def is_available(model_name: str = MODEL_NAME) -> bool:
    """True if the local verifier can run now: the packages and the model are here."""
    return is_installed() and model_is_cached(model_name)


def download_model(model_name: str = MODEL_NAME) -> str:
    """Download the model into the Hugging Face cache; return its local folder.

    `quickstart.py` calls this after it installs the `verify` group.
    """
    from huggingface_hub import snapshot_download

    return snapshot_download(model_name)


class LocalNliVerifier:
    """Entailment probability from an NLI model. The model loads on first use."""

    max_passage_chars = MAX_PASSAGE_CHARS

    def __init__(self, model_name: str = MODEL_NAME, *, batch_size: int = BATCH_SIZE) -> None:
        self.model_name = model_name
        self.name = f"nli:{model_name}"
        self.batch_size = batch_size
        self._tokenizer = None
        self._model = None
        self._entailment_index = 0
        self._lock = threading.Lock()

    def _load(self) -> None:
        with self._lock:
            self._load_once()

    def _load_once(self) -> None:
        if self._model is not None:
            return
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, local_files_only=True)
        self._model = AutoModelForSequenceClassification.from_pretrained(
            self.model_name, local_files_only=True,
        ).eval()
        labels = {int(i): str(label).lower() for i, label in self._model.config.id2label.items()}
        self._entailment_index = next(i for i, label in labels.items() if label.startswith("entail"))

    def entailment(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        import torch

        self._load()
        order = sorted(range(len(pairs)), key=lambda i: len(pairs[i][0]) + len(pairs[i][1]))
        probabilities = [0.0] * len(pairs)
        for first in range(0, len(order), self.batch_size):
            indexes = order[first:first + self.batch_size]
            batch = self._tokenizer(
                [pairs[i][0] for i in indexes], [pairs[i][1] for i in indexes],
                truncation="only_first", max_length=_MAX_TOKENS, padding=True, return_tensors="pt",
            )
            with torch.inference_mode():
                logits = self._model(**batch).logits
            scores = torch.softmax(logits, dim=-1)[:, self._entailment_index].tolist()
            for i, score in zip(indexes, scores):
                probabilities[i] = float(score)
        return probabilities
