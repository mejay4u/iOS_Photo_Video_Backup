import shutil

from PIL import Image

from photobackup import analysis


def scan(lib, tmp_path):
    return analysis.scan_library(lib, analysis.MetricsCache(tmp_path / "cache.sqlite"))


def test_exact_and_similar_duplicates(tmp_path, photo):
    lib = tmp_path / "lib"
    a = photo(lib / "2026" / "Jan 26" / "IMG_0001.JPG", seed=1)
    shutil.copy2(a, lib / "2026" / "Jan 26" / "IMG_0001 (1).JPG")
    # Same picture re-saved smaller -> similar, not exact.
    Image.open(a).resize((400, 300)).save(lib / "2026" / "Jan 26" / "small.jpg", quality=70)
    photo(lib / "2026" / "Jan 26" / "other.jpg", seed=5, size=(600, 800))

    entries = scan(lib, tmp_path)
    groups = analysis.find_duplicates(entries)
    exact = [g for g in groups if g["kind"] == "exact"]
    similar = [g for g in groups if g["kind"] == "similar"]
    assert len(exact) == 1
    assert exact[0]["keep"] == "2026/Jan 26/IMG_0001.JPG"  # the " (1)" copy is the one to delete
    assert len(similar) == 1
    assert {i["path"] for i in similar[0]["items"]} == {"2026/Jan 26/IMG_0001.JPG", "2026/Jan 26/small.jpg"}
    assert similar[0]["keep"] == "2026/Jan 26/IMG_0001.JPG"  # higher resolution wins
    assert not any("other.jpg" in i["path"] for g in groups for i in g["items"])


def test_low_quality_detection(tmp_path, photo):
    lib = tmp_path / "lib"
    photo(lib / "sharp.jpg", seed=1)
    photo(lib / "blurry.jpg", seed=1, blur=8)
    photo(lib / "dark.jpg", brightness=8)
    photo(lib / "tiny.jpg", seed=1, size=(320, 240))

    entries = scan(lib, tmp_path)
    flagged = {e["name"]: e["reasons"] for e in analysis.find_low_quality(entries)}
    assert "sharp.jpg" not in flagged
    assert "blurry" in flagged["blurry.jpg"]
    assert "too dark" in flagged["dark.jpg"]
    assert "low resolution" in flagged["tiny.jpg"]


def test_cache_reused(tmp_path, photo, monkeypatch):
    lib = tmp_path / "lib"
    photo(lib / "a.jpg")
    cache = analysis.MetricsCache(tmp_path / "c.sqlite")
    first = analysis.scan_library(lib, cache)
    monkeypatch.setattr(analysis, "image_metrics", lambda p: (_ for _ in ()).throw(AssertionError("recomputed")))
    second = analysis.scan_library(lib, cache)
    assert first[0]["dhash"] == second[0]["dhash"]


def test_bktree_matches_bruteforce():
    import random
    rnd = random.Random(1)
    values = [rnd.getrandbits(64) for _ in range(300)]
    values += [v ^ (1 << rnd.randrange(64)) for v in values[:50]]
    tree = analysis.BKTree()
    for i, v in enumerate(values):
        tree.add(v, i)
    for i in range(0, len(values), 17):
        expected = {j for j, w in enumerate(values) if analysis.hamming(values[i], w) <= 6}
        assert set(tree.query(values[i], 6)) == expected
