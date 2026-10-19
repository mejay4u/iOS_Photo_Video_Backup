"""Media file detection, capture-date extraction and hashing."""

import hashlib
import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import ExifTags, Image

try:  # HEIC/HEIF support (iPhone default photo format)
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - optional dependency
    HEIF_SUPPORTED = False

PHOTO_EXTS = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".tif", ".tiff",
    ".bmp", ".webp", ".dng",
}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".3gp", ".avi"}
SIDECAR_EXTS = {".aae"}  # iPhone edit instructions; useless on Windows

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]

# Folder the app keeps its own files in (logs, deleted items) inside the library.
APP_DIR_NAME = "_PhotoBackup"
PARTIAL_SUFFIX = ".partial"

_EPOCH_1904 = datetime(1904, 1, 1, tzinfo=timezone.utc)
_MIN_VALID = datetime(1990, 1, 1)


def media_kind(path):
    """Return 'photo', 'video' or None for a path."""
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def is_ignored_dir(name):
    return name.startswith(".") or name == APP_DIR_NAME or name.startswith("$")


def iter_media(root):
    """Yield every photo/video file under root (recursive), skipping app/hidden folders."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not is_ignored_dir(d))
        for name in sorted(filenames):
            if name.startswith(".") or name.endswith(PARTIAL_SUFFIX):
                continue
            if media_kind(name):
                yield Path(dirpath) / name


def _valid(dt):
    return dt is not None and _MIN_VALID <= dt <= datetime.now() + timedelta(days=2)


def _parse_exif_datetime(value):
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    value = str(value).strip().rstrip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def read_photo_date(path):
    """Capture date from EXIF (DateTimeOriginal > DateTimeDigitized > DateTime)."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
    except Exception:
        return None
    try:
        sub = exif.get_ifd(ExifTags.IFD.Exif)
    except Exception:
        sub = {}
    for value in (sub.get(36867), sub.get(36868), exif.get(306)):
        dt = _parse_exif_datetime(value)
        if _valid(dt):
            return dt
    return None


def _find_atom(f, start, end, name):
    """Find a QuickTime/MP4 atom between start and end; return (payload_start, payload_end)."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            return None
        size, kind = struct.unpack(">I4s", header)
        header_len = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return None
            size = struct.unpack(">Q", ext)[0]
            header_len = 16
        elif size == 0:
            size = end - pos
        if size < header_len:
            return None
        if kind == name:
            return pos + header_len, min(pos + size, end)
        pos += size
    return None


def read_video_date(path):
    """Creation time from the MOV/MP4 'mvhd' atom (stored as UTC), in local time."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            moov = _find_atom(f, 0, size, b"moov")
            if not moov:
                return None
            mvhd = _find_atom(f, moov[0], moov[1], b"mvhd")
            if not mvhd:
                return None
            f.seek(mvhd[0])
            version = f.read(4)[0]
            if version == 1:
                secs = struct.unpack(">Q", f.read(8))[0]
            else:
                secs = struct.unpack(">I", f.read(4))[0]
    except (OSError, struct.error, IndexError):
        return None
    if secs == 0:
        return None
    try:
        dt = (_EPOCH_1904 + timedelta(seconds=secs)).astimezone().replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None
    return dt if _valid(dt) else None


def file_time(path):
    """File modified time (preserved by Windows copy; creation time is not)."""
    return datetime.fromtimestamp(os.stat(path).st_mtime)


def get_media_date(path):
    """Return (datetime, source) where source is 'exif', 'video' or 'file'."""
    kind = media_kind(path)
    if kind == "photo":
        dt = read_photo_date(path)
        if dt:
            return dt, "exif"
    elif kind == "video":
        dt = read_video_date(path)
        if dt:
            return dt, "video"
    return file_time(path), "file"


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
