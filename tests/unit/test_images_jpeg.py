"""The images kept from a PDF are JPEG when that weighs less.

opendataloader-pdf writes every image as PNG: the 10 illustrations of `Snow White and the
Seven Dwarfs.pdf` weighed 10 MB. An image goes to JPEG (quality 80) when the JPEG weighs
less than 70 % of the PNG and the image has no transparency; a line drawing, smaller as
PNG, stays PNG."""

import random

import pytest
from PIL import Image

from domain.ingestion.images import compact_images


def _photo(path, size=(300, 300)):
    """A photo-like image: smooth colours with noise, heavy as PNG."""
    rnd = random.Random(1)
    img = Image.new("RGB", size)
    img.putdata([(x % 256, (x * y) % 256, (y + rnd.randint(0, 40)) % 256)
                 for y in range(size[1]) for x in range(size[0])])
    img.save(path)


def test_a_photo_becomes_a_jpeg_and_the_png_goes(tmp_path):
    _photo(tmp_path / "imageFile1.png")
    png = (tmp_path / "imageFile1.png").stat().st_size
    assert compact_images(tmp_path) == {"imageFile1.png": "imageFile1.jpg"}
    assert not (tmp_path / "imageFile1.png").exists()
    assert (tmp_path / "imageFile1.jpg").stat().st_size < 0.7 * png
    with Image.open(tmp_path / "imageFile1.jpg") as img:
        assert img.format == "JPEG"


def test_a_line_drawing_stays_png(tmp_path):
    img = Image.new("RGB", (400, 400), "white")
    for i in range(0, 400, 40):
        for j in range(400):
            img.putpixel((i, j), (0, 0, 0))
    img.save(tmp_path / "imageFile2.png")
    assert compact_images(tmp_path) == {}
    assert (tmp_path / "imageFile2.png").exists() and not (tmp_path / "imageFile2.jpg").exists()


def test_an_image_with_transparency_stays_png(tmp_path):
    _photo(tmp_path / "rgb.png")
    with Image.open(tmp_path / "rgb.png") as rgb:
        rgba = rgb.convert("RGBA")
    rgba.putpixel((0, 0), (0, 0, 0, 0))
    rgba.save(tmp_path / "imageFile3.png")
    (tmp_path / "rgb.png").unlink()
    assert compact_images(tmp_path) == {}


def test_the_conversion_is_deterministic(tmp_path):
    """The same PNG gives the same JPEG bytes, so a description keyed by the sha256 of
    the image is found again when the source is extracted again."""
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        _photo(tmp_path / folder / "imageFile1.png")
        compact_images(tmp_path / folder)
    assert (tmp_path / "a" / "imageFile1.jpg").read_bytes() == (tmp_path / "b" / "imageFile1.jpg").read_bytes()


def test_a_file_that_is_not_an_image_is_left_alone(tmp_path):
    (tmp_path / "imageFile1.png").write_bytes(b"not a png")
    assert compact_images(tmp_path) == {}
    assert (tmp_path / "imageFile1.png").read_bytes() == b"not a png"


@pytest.mark.parametrize("name", ["imageFile1.jpg", "notes.txt"])
def test_only_png_files_are_converted(tmp_path, name):
    (tmp_path / name).write_bytes(b"x")
    assert compact_images(tmp_path) == {}
