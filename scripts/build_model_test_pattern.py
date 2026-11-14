"""Build the pattern PDF of the vision test: one scanned-looking page with no text layer.

    python scripts/build_model_test_pattern.py <output folder>

The page is drawn as an image (text in a serif font, a table of three rows), then given
the look of a scan (a slight rotation, a blur, noise, an uneven background) and saved as
a one-page PDF that holds only that image. One page per language: `patron_es` and `pattern_en`. `<name>.txt` is the text of the page and
`<name>.json` its data, which the test compares with what the vision model reads.
The content is invented: no model can know it from memory.

The product ships only `<name>.pdf` and `<name>.json`, in `base/domain/modeltest/assets/`.
The `.png` and the `.txt` are for inspection. The fonts are those of macOS.
"""

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path("/System/Library/Fonts/Supplemental")
CONTENT = {
    "es": {
        "title": "Acta de recepción del depósito Aldebarán",
        "paragraph": (
            "El depósito Aldebarán recibió el lote ZK-4471 el 14 de marzo de 2031. La encargada, "
            "Marisol Quintanilla Erbes, registró 3.280 cajas en buen estado y 17 cajas con daños. "
            "El valor declarado del lote es de $ 1.946.500,75 y el seguro cubre hasta $ 2.300.000."
        ),
        "header": ("Sector", "Cajas", "Peso (kg)"),
        "rows": (("Norte", "1.120", "8.964"), ("Sur", "1.475", "11.802"), ("Anexo", "685", "5.471")),
        "closing": "El próximo control está previsto para el 9 de abril de 2031.",
        # The values a reading by image tends to change; the test compares each one.
        "data": ["ZK-4471", "14 de marzo de 2031", "Marisol Quintanilla Erbes", "3.280", "17",
                 "1.946.500,75", "2.300.000", "1.120", "8.964", "1.475", "11.802", "685", "5.471",
                 "9 de abril de 2031"],
        "name": "patron_es",
    },
    "en": {
        "title": "Reception record of the Aldebaran warehouse",
        "paragraph": (
            "The Aldebaran warehouse received batch ZK-4471 on 14 March 2031. The manager, "
            "Marisol Quintanilla Erbes, recorded 3,280 boxes in good condition and 17 damaged boxes. "
            "The declared value of the batch is $ 1,946,500.75 and the insurance covers up to $ 2,300,000."
        ),
        "header": ("Sector", "Boxes", "Weight (kg)"),
        "rows": (("North", "1,120", "8,964"), ("South", "1,475", "11,802"), ("Annex", "685", "5,471")),
        "closing": "The next inspection is planned for 9 April 2031.",
        "data": ["ZK-4471", "14 March 2031", "Marisol Quintanilla Erbes", "3,280", "17",
                 "1,946,500.75", "2,300,000", "1,120", "8,964", "1,475", "11,802", "685", "5,471",
                 "9 April 2031"],
        "name": "pattern_en",
    },
}

W, H, MARGIN = 1654, 1170, 150          # 200 dpi, a little more than half an A4 page


def wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    return [*lines, line]


def page(c: dict) -> Image.Image:
    random.seed(4471)
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)
    bold = ImageFont.truetype(str(FONTS / "Times New Roman Bold.ttf"), 46)
    body = ImageFont.truetype(str(FONTS / "Times New Roman.ttf"), 38)
    y = MARGIN
    draw.text((MARGIN, y), c["title"], font=bold, fill=20)
    y += 90
    for line in wrap(draw, c["paragraph"], body, W - 2 * MARGIN):
        draw.text((MARGIN, y), line, font=body, fill=25)
        y += 54
    y += 40
    columns = (MARGIN + 60, MARGIN + 460, MARGIN + 820)
    for i, cell in enumerate(c["header"]):
        draw.text((columns[i], y), cell, font=bold.font_variant(size=38), fill=20)
    y += 56
    draw.line((MARGIN + 40, y, W - MARGIN - 220, y), fill=40, width=2)
    y += 14
    for row in c["rows"]:
        for i, cell in enumerate(row):
            draw.text((columns[i], y), cell, font=body, fill=25)
        y += 54
    draw.line((MARGIN + 40, y + 6, W - MARGIN - 220, y + 6), fill=40, width=2)
    y += 60
    draw.text((MARGIN, y), c["closing"], font=body, fill=25)

    # The look of a scan: rotation, blur, grain and a background that is not even.
    img = img.rotate(0.6, resample=Image.BICUBIC, fillcolor=255).filter(ImageFilter.GaussianBlur(0.7))
    shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: 236 + v // 14)
    img = Image.composite(img, shade, img.point(lambda v: 255 if v < 200 else 0))
    grain = Image.effect_noise((W, H), 9).point(lambda v: v - 128 + 128)
    return Image.blend(img, grain, 0.08)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for language, c in CONTENT.items():
        img = page(c)
        name = c["name"]
        img.save(out / f"{name}.png")
        img.convert("RGB").save(out / f"{name}.pdf", "PDF", resolution=200.0)
        table = "\n".join(" | ".join(row) for row in (c["header"], *c["rows"]))
        (out / f"{name}.txt").write_text(
            f"{c['title']}\n\n{c['paragraph']}\n\n{table}\n\n{c['closing']}\n", encoding="utf-8")
        (out / f"{name}.json").write_text(
            json.dumps({"language": language, "data": c["data"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
