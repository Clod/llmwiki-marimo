"""PDFs built in memory for the OCR tests: no binary file is committed.

`text_pdf` has a text layer, written by hand. `scanned_pdf` has one image per
page and no text layer: each page is a rendering of known text, as a scanner
would produce it.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SPANISH_TEXT = "Cuentos de hadas para la ciudad"  # the real-engine test looks for "ciudad"


def _page_image(text: str) -> Image.Image:
    image = Image.new("RGB", (1240, 400), "white")
    ImageDraw.Draw(image).text((40, 150), text, fill="black", font=ImageFont.load_default(size=56))
    return image


def scanned_pdf(path: Path, texts: list[str]) -> Path:
    """A PDF with one image-only page per entry of `texts`."""
    images = [_page_image(text) for text in texts]
    images[0].save(path, "PDF", resolution=150.0, save_all=True, append_images=images[1:])
    return path


def text_pdf(path: Path, texts: list[str]) -> Path:
    """A PDF with one page per entry of `texts`, each with a text layer."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(texts)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(texts)} >>".encode())
    for i, text in enumerate(texts):
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 200] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode())
        stream = f"BT /F1 18 Tf 40 100 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path
