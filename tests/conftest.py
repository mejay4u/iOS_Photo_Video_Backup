import os
import struct
import sys
from datetime import datetime, timezone

import pytest
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_photo(path, when=None, size=(800, 600), seed=0, blur=0, brightness=None):
    """Create a JPEG with a detailed pattern and optional EXIF DateTimeOriginal."""
    im = Image.new("RGB", size, (brightness,) * 3 if brightness is not None else (120, 140, 160))
    if brightness is None:
        d = ImageDraw.Draw(im)
        step = 20 + seed * 7
        for x in range(0, size[0], step):
            d.line([(x, 0), (size[0] - x, size[1])], fill=(255, (x * 3 + seed * 50) % 255, 40), width=3)
        for y in range(0, size[1], 25):
            d.rectangle([y, y, y + 15 + seed, y + 10], fill=((y * 5) % 255, 20, 200))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    exif = Image.Exif()
    if when:
        exif.get_ifd(0x8769)[36867] = when.strftime("%Y:%m:%d %H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "JPEG", quality=92, exif=exif)
    return path


def make_video(path, when_utc):
    """Minimal QuickTime file: ftyp + mdat + moov/mvhd (moov after mdat like many files)."""
    secs = int((when_utc - datetime(1904, 1, 1, tzinfo=timezone.utc)).total_seconds())
    mvhd_payload = bytes([0, 0, 0, 0]) + struct.pack(">II", secs, secs) + b"\x00" * 88
    mvhd = struct.pack(">I4s", 8 + len(mvhd_payload), b"mvhd") + mvhd_payload
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"qt  \x00\x00\x00\x00"
    mdat = struct.pack(">I4s", 8 + 1000, b"mdat") + os.urandom(1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ftyp + mdat + moov)
    return path


@pytest.fixture
def photo():
    return make_photo


@pytest.fixture
def video():
    return make_video
