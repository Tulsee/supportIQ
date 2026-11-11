"""OCR for scanned PDF pages.

A page is rendered to an image with PDFium (pypdfium2) and read with RapidOCR:
PaddleOCR detection and recognition models on ONNX Runtime. The models ship inside
the package, so OCR needs no system install (no Tesseract) and no download.
"""

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

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


class RapidOcrEngine:
    def __init__(self) -> None:
        self._engine: Any = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "rapidocr"

    def _load(self) -> Any:
        with self._lock:
            if self._engine is None:
                from rapidocr import RapidOCR

                self._engine = RapidOCR()
                logging.getLogger("RapidOCR").setLevel(logging.WARNING)  # chatty at INFO
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
    min_confidence: float = 0.5


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
