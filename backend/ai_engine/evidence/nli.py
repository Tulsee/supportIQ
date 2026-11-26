"""Natural language inference: does a premise entail, contradict, or say nothing
about a hypothesis?

Used to check generated claims against the passages they cite. Default model:
cross-encoder/nli-deberta-v3-xsmall (int8 ONNX, ~90 MB, ~25 ms per pair on CPU),
run directly with ONNX Runtime and the Hugging Face tokenizer.

Small NLI models are strict: a correct paraphrase that adds a detail the premise
doesn't state ("router" for "product") scores as neutral, not entailed. A claim
contradicted by its own source is the more reliable signal.

Pairs are scored one at a time. In a batch, this int8 model's output for a pair shifts
with the other pairs it is padded next to: one claim scored 0.30 contradiction alone and
0.55 beside a longer pair. A score must not depend on what else was checked with it. See
experiments/reports/quillon-v1-evaluation.md (checked: no recorded decision changed).
"""

import json
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import onnxruntime
    from tokenizers import Tokenizer


@dataclass(frozen=True)
class NliScores:
    entailment: float
    neutral: float
    contradiction: float


class NliModel(Protocol):
    @property
    def model_name(self) -> str: ...

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[NliScores]:
        """(premise, hypothesis) pairs -> label probabilities. CPU-bound."""
        ...


class OnnxNliModel:
    def __init__(
        self,
        repo: str,
        *,
        onnx_file: str = "onnx/model_quint8_avx2.onnx",
        cache_dir: Path,
    ) -> None:
        self.repo = repo
        self.onnx_file = onnx_file
        self.cache_dir = cache_dir
        self._loaded: tuple[Tokenizer, onnxruntime.InferenceSession, dict[int, str]] | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self.repo

    def _load(self) -> "tuple[Tokenizer, onnxruntime.InferenceSession, dict[int, str]]":
        with self._lock:
            if self._loaded is None:
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                import onnxruntime as ort
                from huggingface_hub import hf_hub_download
                from tokenizers import Tokenizer

                def fetch(name: str) -> str:
                    return hf_hub_download(self.repo, name, cache_dir=str(self.cache_dir))

                tokenizer = Tokenizer.from_file(fetch("tokenizer.json"))
                tokenizer.enable_truncation(512)
                tokenizer.enable_padding()
                config = json.loads(Path(fetch("config.json")).read_text(encoding="utf-8"))
                labels = {int(k): v.lower() for k, v in config["id2label"].items()}
                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
                session = ort.InferenceSession(
                    fetch(self.onnx_file), options, providers=["CPUExecutionProvider"]
                )
                self._loaded = (tokenizer, session, labels)
            return self._loaded

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[NliScores]:
        if not pairs:
            return []
        import numpy as np

        tokenizer, session, labels = self._load()
        names = {i.name for i in session.get_inputs()}
        results: list[NliScores] = []
        for premise, hypothesis in pairs:  # one at a time: see the module docstring
            [encoded] = tokenizer.encode_batch([(premise, hypothesis)])
            feeds: dict[str, Any] = {
                "input_ids": np.array([encoded.ids], dtype=np.int64),
                "attention_mask": np.array([encoded.attention_mask], dtype=np.int64),
            }
            if "token_type_ids" in names:
                feeds["token_type_ids"] = np.array([encoded.type_ids], dtype=np.int64)
            [logits] = session.run(None, feeds)[0]
            exp = np.exp(logits - logits.max())
            probabilities = exp / exp.sum()
            by_label = {labels[i]: float(p) for i, p in enumerate(probabilities)}
            results.append(
                NliScores(
                    entailment=by_label.get("entailment", 0.0),
                    neutral=by_label.get("neutral", 0.0),
                    contradiction=by_label.get("contradiction", 0.0),
                )
            )
        return results


class OverlapNli:
    """Model-free stand-in for tests: entailment is the share of the hypothesis's
    words found in the premise; a number the premise doesn't contain contradicts."""

    model_name = "test/word-overlap-nli"

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[NliScores]:
        import re

        scores = []
        for premise, hypothesis in pairs:
            words = set(re.findall(r"\w+", premise.lower()))
            hyp = re.findall(r"\w+", hypothesis.lower())
            numbers = [w for w in hyp if w.isdigit()]
            if any(n not in words for n in numbers):
                scores.append(NliScores(0.0, 0.1, 0.9))
                continue
            overlap = sum(w in words for w in hyp) / max(1, len(hyp))
            scores.append(NliScores(overlap, 1 - overlap, 0.0))
        return scores
