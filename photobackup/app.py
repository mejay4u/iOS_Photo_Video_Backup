"""Local web server: REST API + single-page UI. Binds to 127.0.0.1 only."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from . import analysis, organizer
from .media import APP_DIR_NAME, is_ignored_dir, iter_media, media_kind

DEFAULT_SETTINGS = {
    "source": "",
    "library": "",
    "layout": organizer.DEFAULT_LAYOUT,
    "mode": "copy",
    "separate_kinds": False,
    "delete_mode": "recycle",  # 'recycle' (Windows Recycle Bin) or 'folder' (<library>/_PhotoBackup/Deleted)
}


def default_data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".local", "share")
    return Path(base) / "PhotoBackup"


class JobRunner:
    """Runs one background job at a time and exposes its progress."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.state = {"kind": None, "state": "idle"}

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self, kind, fn):
        with self.lock:
            if self.state.get("state") == "running":
                return False
            self.cancel.clear()
            self.state = {"kind": kind, "state": "running", "done": 0, "total": 0,
                          "message": "Starting...", "result": None, "error": None}

        def progress(done, total, message=""):
            with self.lock:
                self.state.update(done=done, total=total, message=message)

        def run():
            try:
                result = fn(progress, self.cancel)
                final = "cancelled" if self.cancel.is_set() else "finished"
                with self.lock:
                    self.state.update(state=final, result=result)
            except organizer.Cancelled:
                with self.lock:
                    self.state.update(state="cancelled")
            except Exception as e:
                traceback.print_exc()
                with self.lock:
                    self.state.update(state="error", error=str(e))

        threading.Thread(target=run, daemon=True).start()
        return True


def create_app(data_dir=None):
    data_dir = Path(data_dir) if data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    settings_path = data_dir / "settings.json"
    thumbs_dir = data_dir / "thumbs"
    thumbs_dir.mkdir(exist_ok=True)

    app = Flask(__name__, static_folder="static", static_url_path="/static")
    jobs = JobRunner()
    cache = analysis.MetricsCache(data_dir / "metrics.sqlite")
    state = {"entries": None, "library": None}

    def load_settings():
        s = dict(DEFAULT_SETTINGS)
        try:
            s.update(json.loads(settings_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return s

    def save_settings(s):
        settings_path.write_text(json.dumps(s, indent=2), encoding="utf-8")

    def library_root():
        lib = load_settings()["library"]
        if not lib or not os.path.isdir(lib):
            abort(400, description="Library folder is not set or does not exist")
        return Path(os.path.realpath(lib))

    def resolve(rel):
        """Map a library-relative path to an absolute one; refuse anything outside the library."""
        root = library_root()
        if not rel or "\x00" in rel:
            abort(400, description="Missing path")
        target = Path(os.path.realpath(root / rel))
        if os.path.commonpath([os.path.normcase(root), os.path.normcase(target)]) != os.path.normcase(root):
            abort(403, description="Path is outside the library")
        return root, target

    @app.before_request
    def local_only():
        # Block DNS-rebinding and cross-site requests: only our own origin may talk to us.
        host = (request.host or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            abort(403)
        if request.method == "POST" and not request.is_json:
            abort(415, description="JSON body required")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(415)
    def handle_error(e):
        return jsonify(error=getattr(e, "description", str(e))), e.code

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    # ---------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        layouts = [{"id": k, "label": v["label"]} for k, v in organizer.LAYOUTS.items()]
        return jsonify(settings=load_settings(), layouts=layouts)

    @app.post("/api/settings")
    def set_settings():
        s = load_settings()
        body = request.get_json()
        for key in DEFAULT_SETTINGS:
            if key in body:
                s[key] = body[key]
        if s["layout"] not in organizer.LAYOUTS:
            abort(400, description="Unknown folder layout")
        if s["mode"] not in ("copy", "move"):
            abort(400, description="Mode must be copy or move")
        if s["delete_mode"] not in ("recycle", "folder"):
            abort(400, description="Unknown delete mode")
        s["source"] = s["source"].strip().strip('"')
        s["library"] = s["library"].strip().strip('"')
        save_settings(s)
        if state["library"] != s["library"]:
            state.update(entries=None, library=None)
        return jsonify(settings=s)

    @app.post("/api/pick-folder")
    def pick_folder():
        """Open a native folder picker (tkinter) in a separate process."""
        code = (
            "import tkinter as tk; from tkinter import filedialog; r = tk.Tk(); r.withdraw();"
            "r.attributes('-topmost', True); print(filedialog.askdirectory() or '')"
        )
        try:
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
            path = out.stdout.strip()
        except Exception:
            path = ""
        return jsonify(path=os.path.normpath(path) if path else "")

    # ------------------------------------------------------------------- jobs
    @app.get("/api/job")
    def job_status():
        return jsonify(jobs.status())

    @app.post("/api/job/cancel")
    def job_cancel():
        jobs.cancel.set()
        return jsonify(ok=True)

    def start_job(kind, fn):
        if not jobs.start(kind, fn):
            abort(400, description="Another task is still running")
        return jsonify(ok=True)

    def checked_settings():
        s = load_settings()
        try:
            organizer.check_paths(s["source"], s["library"])
        except ValueError as e:
            abort(400, description=str(e))
        return s

    @app.post("/api/sort/preview")
    def sort_preview():
        s = checked_settings()

        def run(progress, cancel):
            items, skipped = organizer.build_plan(
                s["source"], s["library"], s["layout"], s["separate_kinds"], progress, cancel)
            return organizer.summarize_plan(items, skipped, s["library"])

        return start_job("preview", run)

    @app.post("/api/sort/start")
    def sort_start():
        s = checked_settings()

        def run(progress, cancel):
            items, skipped = organizer.build_plan(
                s["source"], s["library"], s["layout"], s["separate_kinds"], progress, cancel)
            result = organizer.execute_plan(items, s["library"], s["mode"], progress, cancel)
            result["skipped_count"] = len(skipped)
            state.update(entries=None)
            return result

        return start_job("sort", run)

    @app.post("/api/analyze")
    def analyze():
        root = library_root()

        def run(progress, cancel):
            entries = analysis.scan_library(root, cache, progress, cancel)
            if entries is None:
                raise organizer.Cancelled()
            state.update(entries=entries, library=load_settings()["library"])
            photos = sum(1 for e in entries if e["kind"] == "photo")
            return {"files": len(entries), "photos": photos, "videos": len(entries) - photos,
                    "unreadable": sum(1 for e in entries if e.get("error"))}

        return start_job("analyze", run)

    def require_entries():
        if state["entries"] is None:
            abort(400, description="Run 'Scan library' first")
        return state["entries"]

    @app.get("/api/duplicates")
    def duplicates():
        threshold = max(0, min(20, request.args.get("threshold", analysis.DEFAULT_SIMILARITY, type=int)))
        similar = request.args.get("similar", "1") == "1"
        groups = analysis.find_duplicates(require_entries(), threshold, similar)
        return jsonify(groups=groups)

    @app.get("/api/quality")
    def quality():
        a = request.args
        items = analysis.find_low_quality(
            require_entries(), a.get("blur", type=float), a.get("dark", type=float),
            a.get("bright", type=float), a.get("min_mp", type=float))
        return jsonify(items=items, defaults=analysis.DEFAULT_QUALITY)

    # ---------------------------------------------------------------- library
    @app.get("/api/library/tree")
    def library_tree():
        root = library_root()
        nodes = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted((d for d in dirnames if not is_ignored_dir(d)), reverse=True)
            count = sum(1 for f in filenames if media_kind(f) and not f.startswith("."))
            rel = Path(dirpath).relative_to(root).as_posix()
            nodes[rel] = {"path": "" if rel == "." else rel, "name": Path(dirpath).name,
                          "count": count, "total": count, "children": []}
        for rel in sorted(nodes, key=lambda r: -r.count("/")):
            if rel == ".":
                continue
            parent = rel.rsplit("/", 1)[0] if "/" in rel else "."
            if nodes[rel]["total"] and parent in nodes:
                nodes[parent]["total"] += nodes[rel]["total"]
                nodes[parent]["children"].insert(0, nodes[rel])
        for n in nodes.values():
            n["children"].sort(key=lambda c: c["name"], reverse=True)
        return jsonify(nodes["."])

    @app.get("/api/library/files")
    def library_files():
        root, folder = resolve(request.args.get("folder") or ".")
        if not folder.is_dir():
            abort(404, description="Folder not found")
        files = []
        for p in folder.iterdir():
            if p.is_file() and media_kind(p) and not p.name.startswith("."):
                st = p.stat()
                files.append({"path": p.relative_to(root).as_posix(), "name": p.name,
                              "kind": media_kind(p), "size": st.st_size,
                              "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="minutes")})
        files.sort(key=lambda f: (f["mtime"], f["name"]))
        return jsonify(files=files)

    def render_jpeg(path, max_side, cache_tag):
        st = path.stat()
        key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime}|{max_side}".encode()).hexdigest()
        cached = thumbs_dir / f"{key}.jpg"
        if not cached.exists():
            from PIL import Image, ImageOps
            with Image.open(path) as im:
                im.draft("RGB", (max_side, max_side))
                im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((max_side, max_side))
            buf = BytesIO()
            im.save(buf, "JPEG", quality=85)
            tmp = cached.with_suffix(f".{cache_tag}.tmp")
            tmp.write_bytes(buf.getvalue())
            os.replace(tmp, cached)
        return send_file(cached, mimetype="image/jpeg", max_age=86400)

    @app.get("/api/thumb")
    def thumb():
        _, path = resolve(request.args.get("path"))
        if media_kind(path) != "photo" or not path.is_file():
            return app.send_static_file("video.svg")
        try:
            return render_jpeg(path, 320, threading.get_ident())
        except Exception:
            return app.send_static_file("broken.svg")

    @app.get("/api/preview")
    def preview():
        _, path = resolve(request.args.get("path"))
        if not path.is_file():
            abort(404, description="File not found")
        try:
            return render_jpeg(path, 2048, threading.get_ident())
        except Exception:
            return app.send_static_file("broken.svg")

    @app.get("/api/file")
    def raw_file():
        _, path = resolve(request.args.get("path"))
        if not path.is_file() or not media_kind(path):
            abort(404, description="File not found")
        return send_file(path, conditional=True)

    @app.post("/api/open")
    def open_file():
        """Open a file (or show it in Explorer) with the default Windows app."""
        body = request.get_json()
        _, path = resolve(body.get("path"))
        if not path.exists():
            abort(404, description="File not found")
        if body.get("reveal") and os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # noqa: S606 - local desktop app
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(path.parent if body.get("reveal") else path)])
        return jsonify(ok=True)

    @app.post("/api/delete")
    def delete_files():
        paths = request.get_json().get("paths") or []
        if not isinstance(paths, list) or not paths:
            abort(400, description="No files selected")
        s = load_settings()
        results = []
        for rel in paths:
            root, path = resolve(rel)
            if not path.is_file() or not media_kind(path):
                results.append({"path": rel, "ok": False, "error": "not found"})
                continue
            try:
                if s["delete_mode"] == "folder":
                    dest = root / APP_DIR_NAME / "Deleted" / path.relative_to(root)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    while dest.exists():
                        dest = dest.with_name(f"{dest.stem}~{datetime.now():%H%M%S%f}{dest.suffix}")
                    shutil.move(str(path), str(dest))
                else:
                    from send2trash import send2trash
                    send2trash(str(path))
                cache.forget(path)
                results.append({"path": rel, "ok": True})
            except Exception as e:
                results.append({"path": rel, "ok": False, "error": str(e)})
        deleted = {r["path"] for r in results if r["ok"]}
        if state["entries"] is not None and deleted:
            state["entries"] = [e for e in state["entries"] if e["path"] not in deleted]
        return jsonify(results=results, deleted=len(deleted))

    return app
