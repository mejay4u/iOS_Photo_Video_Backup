"""Plan and execute sorting of photos/videos into date folders.

Safety rules:
  * Files are copied to "<name>.partial", verified by SHA-256, then renamed into place.
  * Existing files are never overwritten: an identical file is skipped, a different
    file with the same name gets a " (1)", " (2)" ... suffix.
  * In "move" mode the source is only removed after the copy has been verified.
"""

import csv
import hashlib
import os
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .media import (
    APP_DIR_NAME, MONTH_ABBR, MONTH_FULL, PARTIAL_SUFFIX, SIDECAR_EXTS,
    get_media_date, media_kind, sha256_file,
)

LAYOUTS = {
    "year/mon-yy": {
        "label": "Year / Month  (2026 / Jan 26)",
        "parts": lambda d: [str(d.year), f"{MONTH_ABBR[d.month - 1]} {d:%y}"],
    },
    "year/mm-month": {
        "label": "Year / Month  (2026 / 01 - January)",
        "parts": lambda d: [str(d.year), f"{d.month:02d} - {MONTH_FULL[d.month - 1]}"],
    },
    "year/mon-yy/date": {
        "label": "Year / Month / Day  (2026 / Jan 26 / 2026-01-26)",
        "parts": lambda d: [str(d.year), f"{MONTH_ABBR[d.month - 1]} {d:%y}", f"{d:%Y-%m-%d}"],
    },
    "year/date": {
        "label": "Year / Day  (2026 / 2026-01-26)",
        "parts": lambda d: [str(d.year), f"{d:%Y-%m-%d}"],
    },
}
DEFAULT_LAYOUT = "year/mon-yy"


class Cancelled(Exception):
    pass


@dataclass
class PlanItem:
    src: Path
    dest_dir: Path
    date: datetime
    date_source: str
    kind: str


def folder_parts(date, layout, kind=None, separate_kinds=False):
    parts = LAYOUTS.get(layout, LAYOUTS[DEFAULT_LAYOUT])["parts"](date)
    if separate_kinds and kind:
        parts.append("Videos" if kind == "video" else "Photos")
    return parts


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def check_paths(source, library):
    """Raise ValueError if the folders are missing or overlap."""
    if not source or not os.path.isdir(source):
        raise ValueError(f"Source folder not found: {source}")
    if not library:
        raise ValueError("Library (backup) folder is not set")
    s, l = _norm(source), _norm(library)
    if s == l or os.path.commonpath([s, l]) in (s, l):
        raise ValueError("Source and library folders must not be the same or inside each other")


def _noop(*_args):
    pass


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def scan_source(source):
    """Return (media_files, skipped_files) found under source."""
    media, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != APP_DIR_NAME)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if name.endswith(PARTIAL_SUFFIX) or name.startswith("."):
                continue
            if media_kind(name):
                media.append(p)
            else:
                reason = "iPhone edit sidecar (.AAE)" if p.suffix.lower() in SIDECAR_EXTS else "not a photo/video"
                skipped.append((p, reason))
    return media, skipped


def build_plan(source, library, layout=DEFAULT_LAYOUT, separate_kinds=False,
               progress=_noop, cancel=None):
    check_paths(source, library)
    files, skipped = scan_source(source)
    library = Path(library)
    dated = []
    for i, path in enumerate(files):
        _check_cancel(cancel)
        progress(i, len(files), f"Reading date: {path.name}")
        try:
            dt, src = get_media_date(path)
        except OSError as e:
            skipped.append((path, f"cannot read: {e}"))
            continue
        dated.append([path, dt, src, media_kind(path)])

    # Live Photos: IMG_1234.HEIC + IMG_1234.MOV must land in the same folder.
    photo_dates = {}
    for path, dt, src, kind in dated:
        if kind == "photo" and src == "exif":
            photo_dates[(str(path.parent), path.stem.lower())] = dt
    for entry in dated:
        path, _dt, _src, kind = entry
        key = (str(path.parent), path.stem.lower())
        if kind == "video" and key in photo_dates:
            entry[1], entry[2] = photo_dates[key], "live-photo"

    items = [
        PlanItem(path, library.joinpath(*folder_parts(dt, layout, kind, separate_kinds)), dt, src, kind)
        for path, dt, src, kind in dated
    ]
    progress(len(files), len(files), "Plan ready")
    return items, skipped


def summarize_plan(items, skipped, library):
    library = Path(library)
    folders = Counter()
    for it in items:
        folders[it.dest_dir.relative_to(library).as_posix()] += 1
    return {
        "total": len(items),
        "photos": sum(1 for it in items if it.kind == "photo"),
        "videos": sum(1 for it in items if it.kind == "video"),
        "date_sources": dict(Counter(it.date_source for it in items)),
        "folders": [{"folder": f, "count": c} for f, c in sorted(folders.items())],
        "skipped": [{"path": str(p), "reason": r} for p, r in skipped[:500]],
        "skipped_count": len(skipped),
    }


def _copy_with_hash(src, dst):
    """Copy src to dst returning the SHA-256 of the bytes read; preserves timestamps."""
    h = hashlib.sha256()
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            block = fin.read(1024 * 1024)
            if not block:
                break
            h.update(block)
            fout.write(block)
        fout.flush()
        os.fsync(fout.fileno())
    shutil.copystat(src, dst)
    return h.hexdigest()


def _next_free_name(dest_dir, name, taken):
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        candidate = dest_dir / f"{stem} ({n}){ext}"
        if candidate.name.lower() not in taken and not candidate.exists():
            return candidate
        n += 1


def transfer_one(item, mode="copy"):
    """Copy/move one file safely. Returns (status, target_path).

    status: 'copied' | 'moved' | 'renamed' | 'already-backed-up'
    """
    item.dest_dir.mkdir(parents=True, exist_ok=True)
    target = item.dest_dir / item.src.name
    src_size = item.src.stat().st_size
    src_hash = None
    renamed = False
    seen = set()
    while target.exists():
        if target.stat().st_size == src_size:
            src_hash = src_hash or sha256_file(item.src)
            if sha256_file(target) == src_hash:
                if mode == "move":
                    os.remove(item.src)
                return "already-backed-up", target
        seen.add(target.name.lower())
        target = _next_free_name(item.dest_dir, item.src.name, seen)
        renamed = True

    tmp = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        copied_hash = _copy_with_hash(item.src, tmp)
        if src_hash and copied_hash != src_hash:
            raise IOError("source changed while copying")
        if sha256_file(tmp) != copied_hash:
            raise IOError("verification failed: copied file differs from source")
        if target.exists():  # appeared meanwhile; never overwrite
            raise IOError(f"target appeared during copy: {target}")
        os.replace(tmp, target)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    if mode == "move":
        os.remove(item.src)
        return ("renamed" if renamed else "moved"), target
    return ("renamed" if renamed else "copied"), target


def execute_plan(items, library, mode="copy", progress=_noop, cancel=None):
    if mode not in ("copy", "move"):
        raise ValueError("mode must be 'copy' or 'move'")
    library = Path(library)
    log_dir = library / APP_DIR_NAME / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"sort-{datetime.now():%Y%m%d-%H%M%S}.csv"
    counts = defaultdict(int)
    errors = []
    with open(log_path, "w", newline="", encoding="utf-8") as logf:
        log = csv.writer(logf)
        log.writerow(["status", "source", "destination", "date", "date_source", "detail"])
        for i, item in enumerate(items):
            if cancel is not None and cancel.is_set():
                counts["cancelled"] = len(items) - i
                break
            progress(i, len(items), f"{mode.title()}: {item.src.name}")
            try:
                status, target = transfer_one(item, mode)
                counts[status] += 1
                log.writerow([status, item.src, target, item.date.isoformat(sep=" "), item.date_source, ""])
            except Exception as e:  # keep going; every failure is reported
                counts["error"] += 1
                errors.append({"path": str(item.src), "error": str(e)})
                log.writerow(["error", item.src, "", item.date.isoformat(sep=" "), item.date_source, str(e)])
            logf.flush()
    progress(len(items), len(items), "Done")
    return {"counts": dict(counts), "errors": errors[:500], "log": str(log_path)}
