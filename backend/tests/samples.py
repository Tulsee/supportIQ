"""Sample documents for tests, generated on the fly (nothing binary is committed)."""

import io
import zipfile


def _pdf_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(
    pages: list[list[str]], *, encrypt_with: str | None = None, owner_password: str | None = None
) -> bytes:
    """A real PDF with one text line per entry; an empty page has no text (like a
    scanned page). Optionally AES-encrypted: ``encrypt_with`` is the password needed
    to open it ("" for a permissions-only PDF that anyone can open)."""
    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        content_id = page_ids[i] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"]
        for line in lines:
            ops += [f"({_pdf_string(line)}) Tj", "T*"]
        ops.append("ET")
        stream = "\n".join(ops).encode() if lines else b""
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    data = out.getvalue()
    if encrypt_with is None:
        return data

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=encrypt_with, owner_password=owner_password, algorithm="AES-128")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    return encrypted.getvalue()


def make_scanned_pdf(pages: list[list[str]]) -> bytes:
    """Image-only pages (like a scan): the text exists only as pixels."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=40)
    images = []
    for lines in pages:
        image = Image.new("RGB", (1700, 2200), "white")
        draw = ImageDraw.Draw(image)
        for i, line in enumerate(lines):
            draw.text((120, 150 + i * 90), line, fill="black", font=font)
        images.append(image)
    out = io.BytesIO()
    images[0].save(out, "PDF", resolution=200, save_all=True, append_images=images[1:])
    return out.getvalue()


def make_docx(
    sections: list[tuple[str, list[str]]], *, table: list[list[str]] | None = None
) -> bytes:
    """A Word document: (heading, paragraphs) sections, optionally a table at the end."""
    from docx import Document

    document = Document()
    for heading, paragraphs in sections:
        document.add_heading(heading, level=1)
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.rows[r].cells[c].text = value
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def make_zip_bomb_docx() -> bytes:
    """Looks like a .docx, but word/document.xml inflates hugely."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", b"\0" * (50 * 1024 * 1024))
    return out.getvalue()


HANDBOOK_MD = """# Refund policy

Customers can request a refund within 30 days of purchase. Refunds go back to the
original payment method. Processing takes five business days.

## Damaged items

If an item arrives damaged, send a photo to support. We replace damaged items
free of charge, including shipping.

# Shipping

We ship worldwide from our warehouse in Rotterdam. Standard delivery takes
three to seven business days. Express shipping is available at checkout.
"""
