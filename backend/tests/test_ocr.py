"""The real OCR engines (RapidOCR) on scans.

The default model ships with the package. The Devanagari model (~18 MB) is
downloaded into MODEL_CACHE_DIR on first run; CI caches it.
"""

import io
from pathlib import Path

import pytest
from PIL import Image

from ai_engine.ingestion.extract import DocumentType, detect_type, extract
from ai_engine.ingestion.ocr import OcrOptions, RapidOcrEngine
from app.core.config import settings
from tests.samples import make_scanned_pdf

# Printed Nepali (rendered with proper Devanagari shaping), as a scanner would see it.
NEPALI_SCAN = Path(__file__).parent / "fixtures" / "nepali-printed.png"


@pytest.fixture(scope="module")
def engine() -> RapidOcrEngine:
    return RapidOcrEngine()


@pytest.fixture(scope="module")
def devanagari() -> RapidOcrEngine:
    return RapidOcrEngine("devanagari", model_dir=settings.model_cache_dir / "rapidocr")


def image_pdf(path: Path) -> bytes:
    buffer = io.BytesIO()
    Image.open(path).convert("RGB").save(buffer, format="PDF", resolution=150)
    return buffer.getvalue()


def ocr_text(engine: RapidOcrEngine, pdf: bytes) -> str:
    document = extract(
        DocumentType.PDF, pdf, max_pages=10, max_characters=100_000, ocr=OcrOptions(engine)
    )
    return "\n".join(block.text for block in document.blocks)


def test_scanned_pdf_is_read_with_ocr(engine: RapidOcrEngine) -> None:
    pdf = make_scanned_pdf(
        [
            ["Warranty claims must be filed within 14 days."],
            ["Error code E-4012 means the fan has stopped.", "Replace the fan module."],
        ]
    )
    kind = detect_type("scan.pdf", pdf)

    document = extract(kind, pdf, max_pages=10, max_characters=100_000, ocr=OcrOptions(engine))

    assert kind is DocumentType.PDF
    assert document.ocr_pages == 2
    by_page = {block.page: block.text for block in document.blocks}
    assert "within 14 days" in by_page[1]
    assert "E-4012" in by_page[2] and "fan module" in by_page[2]


def test_the_devanagari_model_reads_printed_nepali_and_english(
    engine: RapidOcrEngine, devanagari: RapidOcrEngine
) -> None:
    pdf = image_pdf(NEPALI_SCAN)

    text = ocr_text(devanagari, pdf)

    assert "राउटरको बत्ती रातो भएमा" in text
    assert "ग्राहक सेवा बिहान सात" in text
    assert "बिल नम्बर: २०८१/०८२" in text
    assert "H & H Builders Pvt. Ltd., Kalanki, Kathmandu" in text
    # The default model can't read Devanagari at all.
    with pytest.raises(Exception, match="even with OCR"):
        ocr_text(engine, image_pdf_without_english(pdf))


def image_pdf_without_english(pdf: bytes) -> bytes:
    """The Nepali lines only: the fixture minus its last (English) line."""
    image = Image.open(NEPALI_SCAN)
    buffer = io.BytesIO()
    image.crop((0, 0, image.width, image.height - 90)).convert("RGB").save(
        buffer, format="PDF", resolution=150
    )
    return buffer.getvalue()


def test_the_devanagari_model_reads_english_scans(devanagari: RapidOcrEngine) -> None:
    pdf = make_scanned_pdf(
        [["Error code E-4012 means the fan has stopped.", "Replace the fan module."]]
    )

    text = ocr_text(devanagari, pdf)

    assert "E-4012" in text and "fan module" in text
