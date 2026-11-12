"""OCR for scanned PDF pages.

A page is rendered to an image with PDFium (pypdfium2) and read with RapidOCR:
PaddleOCR detection and recognition models on ONNX Runtime, with no system install
(no Tesseract). Two recognition models are available:

- ``devanagari``: PP-OCRv5 Devanagari (Nepali, Hindi, Marathi), which also reads
  English and digits as well as the default model. Downloaded once (about 18 MB)
  into the model cache on first use.
- ``default``: the model bundled with RapidOCR (PP-OCRv6: Chinese, Japanese,
  English and Latin-script languages). It cannot read Devanagari.

The text-line orientation classifier is turned off for Devanagari: it flips
Devanagari lines (the headline bar reads as the bottom of upside-down text).

These models read printed text. Handwriting comes out as low-confidence noise,
which ``extract`` filters out and reports.
"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float


class OcrEngine(Protocol):
    @property
    def name(self) -> str: ...

    def read(self, image: "np.ndarray") -> list[OcrLine]:
        """Text lines in reading order. CPU-bound: call from a worker thread."""
        ...


OcrScript = Literal["devanagari", "default"]


class RapidOcrEngine:
    def __init__(self, script: OcrScript = "default", *, model_dir: Path | None = None) -> None:
        self.script = script
        self.model_dir = model_dir  # where downloaded models are kept (Devanagari)
        self._engine: Any = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"rapidocr/{self.script}"

    def _params(self) -> dict[str, Any]:
        if self.script == "default":
            return {}  # the bundled models
        from rapidocr import LangRec, ModelType, OCRVersion

        params: dict[str, Any] = {
            "Rec.lang_type": LangRec.DEVANAGARI,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Global.use_cls": False,
        }
        if self.model_dir is not None:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            params["Global.model_root_dir"] = str(self.model_dir)
        return params

    def _load(self) -> Any:
        with self._lock:
            if self._engine is None:
                logging.getLogger("RapidOCR").setLevel(logging.WARNING)  # chatty at INFO
                from rapidocr import RapidOCR

                self._engine = RapidOCR(params=self._params())
            return self._engine

    def read(self, image: "np.ndarray") -> list[OcrLine]:
        engine = self._load()
        with self._lock:  # one inference at a time per process
            result = engine(image)
        texts = result.txts or ()
        scores = result.scores or ()
        return [OcrLine(text, float(score)) for text, score in zip(texts, scores, strict=False)]


@dataclass(frozen=True)
class OcrOptions:
    engine: OcrEngine
    min_text_chars: int = 20  # pages with less extractable text are read with OCR
    max_pages: int = 200  # bounds the work per document (about 1-2 s per page)
    dpi: int = 200
    # Lines below this confidence, or with fewer than two letters or digits, are
    # dropped. Printed text scores 0.9-1.0; handwriting mostly 0.5-0.8.
    min_confidence: float = 0.8
    # A page that loses at least this share of its lines is reported as unreadable.
    unreadable_share: float = 0.4


_MAX_SIDE_PIXELS = 4000  # cap rendering for unusually large pages


def render_page(pdf: Any, index: int, *, dpi: int) -> "np.ndarray":
    """Page ``index`` of a ``pypdfium2.PdfDocument`` as an RGB array."""
    page = pdf[index]
    try:
        width, height = page.get_size()  # in points (1/72 inch)
        scale = min(dpi / 72, _MAX_SIDE_PIXELS / max(width, height, 1))
        return page.render(scale=scale).to_numpy()
    finally:
        page.close()
