import time
from datetime import datetime

import pytest

from photobackup.app import create_app

HOST = {"base_url": "http://127.0.0.1:8765"}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "data")
    c = app.test_client()
    c.environ_base["HTTP_HOST"] = "127.0.0.1:8765"
    return c


def configure(client, src, lib, **extra):
    src.mkdir(exist_ok=True)
    lib.mkdir(exist_ok=True)
    r = client.post("/api/settings", json={"source": str(src), "library": str(lib), **extra})
    assert r.status_code == 200, r.json


def wait_job(client):
    for _ in range(200):
        s = client.get("/api/job").json
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_rejects_foreign_host_and_non_json(client):
    assert client.get("/api/settings", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/delete", data="paths=x").status_code == 415


def test_full_flow(client, tmp_path, photo):
    src, lib = tmp_path / "src", tmp_path / "lib"
    photo(src / "IMG_0001.JPG", datetime(2026, 1, 26), seed=1)
    photo(src / "IMG_0002.JPG", datetime(2026, 1, 27), seed=1, blur=8)
    configure(client, src, lib, delete_mode="folder")

    assert client.post("/api/sort/preview", json={}).status_code == 200
    s = wait_job(client)
    assert s["state"] == "finished" and s["result"]["total"] == 2
    assert not lib.joinpath("2026").exists()  # preview copies nothing

    client.post("/api/sort/start", json={})
    s = wait_job(client)
    assert s["result"]["counts"] == {"copied": 2}

    tree = client.get("/api/library/tree").json
    assert tree["total"] == 2 and tree["children"][0]["name"] == "2026"
    files = client.get("/api/library/files?folder=2026/Jan 26").json["files"]
    assert [f["name"] for f in files] == ["IMG_0001.JPG", "IMG_0002.JPG"]
    assert client.get("/api/thumb?path=2026/Jan 26/IMG_0001.JPG").mimetype == "image/jpeg"

    client.post("/api/analyze", json={})
    assert wait_job(client)["state"] == "finished"
    q = client.get("/api/quality").json["items"]
    assert [i["name"] for i in q] == ["IMG_0002.JPG"]

    r = client.post("/api/delete", json={"paths": ["2026/Jan 26/IMG_0002.JPG"]}).json
    assert r["deleted"] == 1
    assert (lib / "_PhotoBackup" / "Deleted" / "2026" / "Jan 26" / "IMG_0002.JPG").exists()
    assert client.get("/api/quality").json["items"] == []


def test_path_traversal_blocked(client, tmp_path, photo):
    src, lib = tmp_path / "src", tmp_path / "lib"
    configure(client, src, lib)
    secret = photo(tmp_path / "secret.jpg")
    for path in ("../secret.jpg", str(secret)):
        assert client.get(f"/api/file?path={path}").status_code == 403
        assert client.post("/api/delete", json={"paths": [path]}).status_code == 403
    assert secret.exists()


def test_sort_with_bad_folders_reports_error(client, tmp_path):
    client.post("/api/settings", json={"source": str(tmp_path / "nope"), "library": str(tmp_path)})
    r = client.post("/api/sort/start", json={})
    assert r.status_code == 400 and "not found" in r.json["error"]
