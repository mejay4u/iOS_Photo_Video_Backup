"""Duplicate detection and low-quality photo detection.

* Exact duplicates: same size + same SHA-256 (photos and videos).
* Similar photos: 64-bit difference hash (dHash) within a Hamming distance.
* Low quality: blur (variance of Laplacian), too dark / too bright, very low resolution.

Per-file metrics are cached in SQLite keyed by (path, size, mtime) so rescans are fast.
"""

import os
import re
import sqlite3
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .media import iter_media, media_kind, sha256_file

ANALYSIS_SIZE = 1024  # images are normalised to this long side before measuring
_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)

DEFAULT_QUALITY = {"blur": 60.0, "dark": 30.0, "bright": 235.0, "min_mp": 0.3}
DEFAULT_SIMILARITY = 5


class MetricsCache:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY, size INTEGER, mtime REAL,
                sha256 TEXT, dhash TEXT, width INTEGER, height INTEGER,
                sharpness REAL, brightness REAL, error TEXT)"""
        )
        self._db.commit()

    def get(self, path, size, mtime):
        with self._lock:
            row = self._db.execute(
                "SELECT sha256, dhash, width, height, sharpness, brightness, error "
                "FROM files WHERE path=? AND size=? AND mtime=?",
                (str(path), size, mtime),
            ).fetchone()
        if not row:
            return {}
        keys = ("sha256", "dhash", "width", "height", "sharpness", "brightness", "error")
        return {k: v for k, v in zip(keys, row) if v is not None}

    def put(self, path, size, mtime, data):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?)",
                (str(path), size, mtime, data.get("sha256"), data.get("dhash"),
                 data.get("width"), data.get("height"), data.get("sharpness"),
                 data.get("brightness"), data.get("error")),
            )
            self._db.commit()

    def forget(self, path):
        with self._lock:
            self._db.execute("DELETE FROM files WHERE path=?", (str(path),))
            self._db.commit()


def image_metrics(path):
    """Return width, height, dhash (hex), sharpness and brightness for an image."""
    with Image.open(path) as im:
        width, height = im.size
        im.draft("L", (ANALYSIS_SIZE, ANALYSIS_SIZE))  # fast JPEG decode at reduced size
        im = ImageOps.exif_transpose(im)
        gray = im.convert("L")
    gray.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE))
    sharpness = ImageStat.Stat(gray.filter(_LAPLACIAN)).var[0]
    brightness = ImageStat.Stat(gray).mean[0]
    small = gray.resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (small[row * 9 + col] > small[row * 9 + col + 1])
    return {
        "width": width, "height": height, "dhash": f"{bits:016x}",
        "sharpness": round(sharpness, 2), "brightness": round(brightness, 2),
    }


def hamming(a, b):
    return bin(a ^ b).count("1")


class BKTree:
    def __init__(self):
        self.root = None  # (value, ids, children)

    def add(self, value, ident):
        if self.root is None:
            self.root = (value, [ident], {})
            return
        node = self.root
        while True:
            d = hamming(value, node[0])
            if d == 0:
                node[1].append(ident)
                return
            if d in node[2]:
                node = node[2][d]
            else:
                node[2][d] = (value, [ident], {})
                return

    def query(self, value, radius):
        out, stack = [], [self.root] if self.root else []
        while stack:
            node = stack.pop()
            d = hamming(value, node[0])
            if d <= radius:
                out.extend(node[1])
            for k, child in node[2].items():
                if d - radius <= k <= d + radius:
                    stack.append(child)
        return out


def scan_library(library, cache, progress=lambda *a: None, cancel=None, workers=4):
    """Collect metrics for every media file in the library. Returns list of dicts."""
    library = Path(library)
    files = list(iter_media(library))
    entries = []
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append({
            "abs": p, "path": p.relative_to(library).as_posix(), "name": p.name,
            "kind": media_kind(p), "size": st.st_size, "mtime": st.st_mtime,
        })

    # Only files sharing a size with another file can be exact duplicates.
    by_size = defaultdict(list)
    for e in entries:
        by_size[e["size"]].append(e)
    need_hash = {id(e) for group in by_size.values() if len(group) > 1 for e in group}

    done = [0]
    lock = threading.Lock()

    def work(e):
        if cancel is not None and cancel.is_set():
            return
        cached = cache.get(e["abs"], e["size"], e["mtime"])
        data = dict(cached)
        try:
            if e["kind"] == "photo" and "dhash" not in data and "error" not in data:
                data.update(image_metrics(e["abs"]))
            if id(e) in need_hash and "sha256" not in data:
                data["sha256"] = sha256_file(e["abs"])
        except Exception as ex:
            data["error"] = str(ex)[:300]
        if data != cached:
            cache.put(e["abs"], e["size"], e["mtime"], data)
        e.update(data)
        with lock:
            done[0] += 1
            progress(done[0], len(entries), f"Analysing: {e['name']}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, entries))
    if cancel is not None and cancel.is_set():
        return None
    for e in entries:
        e.pop("abs")
    return entries


_COPY_SUFFIX = re.compile(r"( \(\d+\)|-copy| copy)$", re.IGNORECASE)


def _exact_keep_key(e):
    stem = os.path.splitext(e["name"])[0]
    return (bool(_COPY_SUFFIX.search(stem)), e["mtime"], len(e["path"]), e["path"])


def _similar_keep_key(e):
    return (-(e.get("width", 0) * e.get("height", 0)), -e.get("sharpness", 0), -e["size"], e["path"])


def find_duplicates(entries, threshold=DEFAULT_SIMILARITY, include_similar=True):
    """Group duplicates. Each group: {kind, keep, items}."""
    groups = []
    in_exact = {}
    by_hash = defaultdict(list)
    for e in entries:
        if e.get("sha256"):
            by_hash[(e["size"], e["sha256"])].append(e)
    for members in by_hash.values():
        if len(members) > 1:
            members.sort(key=_exact_keep_key)
            for m in members:
                in_exact[m["path"]] = members[0]["path"]
            groups.append({"kind": "exact", "keep": members[0]["path"], "items": members})

    if include_similar:
        # Compare one representative per exact group so copies don't form fake groups.
        photos = [e for e in entries if e.get("dhash") and in_exact.get(e["path"], e["path"]) == e["path"]]
        values = [int(e["dhash"], 16) for e in photos]
        tree = BKTree()
        for i, v in enumerate(values):
            tree.add(v, i)
        parent = list(range(len(photos)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, v in enumerate(values):
            for j in tree.query(v, threshold):
                if j != i:
                    parent[find(i)] = find(j)
        clusters = defaultdict(list)
        for i, e in enumerate(photos):
            clusters[find(i)].append(e)
        for members in clusters.values():
            if len(members) > 1:
                members = sorted((dict(m) for m in members), key=_similar_keep_key)
                base = int(members[0]["dhash"], 16)
                for m in members:
                    m["distance"] = hamming(base, int(m["dhash"], 16))
                groups.append({"kind": "similar", "keep": members[0]["path"], "items": members})

    groups.sort(key=lambda g: (g["kind"] != "exact", g["items"][0]["path"]))
    return groups


def find_low_quality(entries, blur=None, dark=None, bright=None, min_mp=None):
    t = dict(DEFAULT_QUALITY)
    for k, v in (("blur", blur), ("dark", dark), ("bright", bright), ("min_mp", min_mp)):
        if v is not None:
            t[k] = float(v)
    results = []
    for e in entries:
        if e["kind"] != "photo" or "sharpness" not in e:
            continue
        reasons = []
        if e["sharpness"] < t["blur"]:
            reasons.append("blurry")
        if e["brightness"] < t["dark"]:
            reasons.append("too dark")
        if e["brightness"] > t["bright"]:
            reasons.append("overexposed")
        if e["width"] * e["height"] / 1e6 < t["min_mp"]:
            reasons.append("low resolution")
        if reasons:
            results.append(dict(e, reasons=reasons))
    results.sort(key=lambda e: e["sharpness"])
    return results
