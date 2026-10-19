import os
from datetime import datetime, timezone

from photobackup.media import get_media_date, iter_media, read_photo_date, read_video_date


def test_photo_exif_date(tmp_path, photo):
    p = photo(tmp_path / "IMG_0001.JPG", datetime(2026, 1, 26, 14, 3, 9))
    assert read_photo_date(p) == datetime(2026, 1, 26, 14, 3, 9)
    assert get_media_date(p) == (datetime(2026, 1, 26, 14, 3, 9), "exif")


def test_photo_without_exif_falls_back_to_file_time(tmp_path, photo):
    p = photo(tmp_path / "screenshot.jpg")
    ts = datetime(2025, 7, 4, 10, 0).timestamp()
    os.utime(p, (ts, ts))
    assert get_media_date(p) == (datetime(2025, 7, 4, 10, 0), "file")


def test_video_mvhd_date_is_converted_to_local_time(tmp_path, video):
    utc = datetime(2026, 1, 26, 20, 0, tzinfo=timezone.utc)
    p = video(tmp_path / "IMG_0002.MOV", utc)
    assert read_video_date(p) == utc.astimezone().replace(tzinfo=None)
    assert get_media_date(p)[1] == "video"


def test_corrupt_video_falls_back(tmp_path):
    p = tmp_path / "bad.mp4"
    p.write_bytes(b"\x00\x00\x00\x02junk")
    assert read_video_date(p) is None
    assert get_media_date(p)[1] == "file"


def test_iter_media_skips_app_folder_and_non_media(tmp_path, photo):
    photo(tmp_path / "a" / "x.jpg")
    photo(tmp_path / "_PhotoBackup" / "Deleted" / "y.jpg")
    (tmp_path / "a" / "x.AAE").write_text("<plist/>")
    (tmp_path / "a" / "z.jpg.partial").write_bytes(b"")
    assert [p.name for p in iter_media(tmp_path)] == ["x.jpg"]
