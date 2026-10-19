import shutil
from datetime import datetime, timezone

import pytest

from photobackup import organizer
from photobackup.media import sha256_file


def run(src, lib, mode="copy", layout=organizer.DEFAULT_LAYOUT, **kw):
    items, skipped = organizer.build_plan(src, lib, layout, **kw)
    return organizer.execute_plan(items, lib, mode), skipped


def test_layouts():
    d = datetime(2026, 1, 26)
    assert organizer.folder_parts(d, "year/mon-yy") == ["2026", "Jan 26"]
    assert organizer.folder_parts(d, "year/mm-month") == ["2026", "01 - January"]
    assert organizer.folder_parts(d, "year/mon-yy/date") == ["2026", "Jan 26", "2026-01-26"]
    assert organizer.folder_parts(d, "year/date", "video", True) == ["2026", "2026-01-26", "Videos"]


def test_copy_sorts_by_date_and_keeps_originals(tmp_path, photo, video):
    src, lib = tmp_path / "src", tmp_path / "lib"
    a = photo(src / "DCIM" / "100APPLE" / "IMG_0001.JPG", datetime(2026, 1, 26, 9))
    photo(src / "DCIM" / "100APPLE" / "IMG_0002.JPG", datetime(2025, 12, 31, 23, 59))
    video(src / "DCIM" / "100APPLE" / "IMG_0003.MOV", datetime(2026, 1, 15, 12, tzinfo=timezone.utc))
    (src / "DCIM" / "100APPLE" / "IMG_0001.AAE").write_text("x")

    result, skipped = run(src, lib)
    assert result["counts"] == {"copied": 3}
    assert (lib / "2026" / "Jan 26" / "IMG_0001.JPG").exists()
    assert (lib / "2025" / "Dec 25" / "IMG_0002.JPG").exists()
    assert (lib / "2026" / "Jan 26" / "IMG_0003.MOV").exists()
    assert sha256_file(lib / "2026" / "Jan 26" / "IMG_0001.JPG") == sha256_file(a)
    assert a.exists()  # copy mode never touches the source
    assert [r for _, r in skipped] == ["iPhone edit sidecar (.AAE)"]
    assert not list(lib.rglob("*.partial"))
    assert (lib / "_PhotoBackup" / "logs").glob("sort-*.csv")


def test_rerun_skips_already_backed_up(tmp_path, photo):
    src, lib = tmp_path / "src", tmp_path / "lib"
    photo(src / "IMG_0001.JPG", datetime(2026, 1, 26))
    run(src, lib)
    result, _ = run(src, lib)
    assert result["counts"] == {"already-backed-up": 1}
    assert len(list((lib / "2026" / "Jan 26").iterdir())) == 1


def test_name_clash_never_overwrites(tmp_path, photo):
    src, lib = tmp_path / "src", tmp_path / "lib"
    existing = photo(lib / "2026" / "Jan 26" / "IMG_0001.JPG", datetime(2026, 1, 26), seed=1)
    before = sha256_file(existing)
    photo(src / "IMG_0001.JPG", datetime(2026, 1, 26), seed=2)
    result, _ = run(src, lib)
    assert result["counts"] == {"renamed": 1}
    assert sha256_file(existing) == before
    assert (lib / "2026" / "Jan 26" / "IMG_0001 (1).JPG").exists()


def test_move_removes_source_only_after_copy(tmp_path, photo):
    src, lib = tmp_path / "src", tmp_path / "lib"
    p = photo(src / "IMG_0001.JPG", datetime(2026, 3, 1))
    digest = sha256_file(p)
    result, _ = run(src, lib, mode="move")
    assert result["counts"] == {"moved": 1}
    assert not p.exists()
    assert sha256_file(lib / "2026" / "Mar 26" / "IMG_0001.JPG") == digest


def test_failed_copy_keeps_source_and_leaves_no_partial(tmp_path, photo, monkeypatch):
    src, lib = tmp_path / "src", tmp_path / "lib"
    p = photo(src / "IMG_0001.JPG", datetime(2026, 3, 1))
    monkeypatch.setattr(organizer, "sha256_file", lambda path: "corrupted")
    result, _ = run(src, lib, mode="move")
    assert result["counts"] == {"error": 1}
    assert p.exists()
    assert not list(lib.rglob("*.JPG*"))


def test_live_photo_video_follows_photo_date(tmp_path, photo, video):
    src, lib = tmp_path / "src", tmp_path / "lib"
    photo(src / "IMG_0010.HEIC.jpg", datetime(2026, 1, 31, 23, 59))  # unrelated name
    photo(src / "IMG_0011.JPG", datetime(2026, 1, 31, 23, 59, 58))
    # Video metadata says February (e.g. UTC rollover) - must still go to January with its photo.
    video(src / "IMG_0011.MOV", datetime(2026, 2, 1, 12, tzinfo=timezone.utc))
    items, _ = organizer.build_plan(src, lib)
    mov = next(i for i in items if i.src.name == "IMG_0011.MOV")
    assert mov.date_source == "live-photo"
    assert mov.dest_dir == lib / "2026" / "Jan 26"


def test_overlapping_folders_rejected(tmp_path):
    (tmp_path / "lib").mkdir()
    with pytest.raises(ValueError):
        organizer.check_paths(tmp_path, tmp_path / "lib")
    with pytest.raises(ValueError):
        organizer.check_paths(tmp_path / "lib", tmp_path)
    with pytest.raises(ValueError):
        organizer.check_paths(tmp_path / "missing", tmp_path / "lib")


def test_duplicate_files_in_source_stored_once(tmp_path, photo):
    src, lib = tmp_path / "src", tmp_path / "lib"
    p = photo(src / "a" / "IMG_0001.JPG", datetime(2026, 1, 26))
    (src / "b").mkdir()
    shutil.copy2(p, src / "b" / "IMG_0001.JPG")
    result, _ = run(src, lib)
    assert result["counts"] == {"copied": 1, "already-backed-up": 1}
