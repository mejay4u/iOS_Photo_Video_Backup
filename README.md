# iPhone Photo Backup (Windows, runs locally)

Backs up iPhone photos and videos into date folders on your PC, and gives you a
browser-based control panel to browse, find duplicates, review low-quality
photos and delete what you don't want.

```
E:\Photos\
├── 2025\
│   └── Dec 25\        ← every photo + video taken in December 2025
└── 2026\
    ├── Jan 26\
    └── Feb 26\
```

Everything runs on your own computer at `http://127.0.0.1:8765` — no cloud, no
account, nothing uploaded anywhere.

## Features

| Tab | What it does |
|---|---|
| **Backup & Sort** | Reads each file's real capture date (photo EXIF, video metadata) and copies it into `Year\Month` folders. Preview first, then start. |
| **Library** | Browse the backed-up library by year/month, view photos and play videos, select and delete. |
| **Duplicates** | Finds **exact** copies (byte-identical, photos + videos) and **similar** photos (bursts, re-saves, edits). Suggests which to keep. |
| **Low Quality** | Finds **blurry**, **too dark**, **overexposed** and **very low resolution** photos, worst first, for review. |

Folder layouts to choose from: `2026\Jan 26` (default), `2026\01 - January`,
`2026\Jan 26\2026-01-26` (one folder per day), `2026\2026-01-26`. You can also
put photos and videos into separate `Photos` / `Videos` subfolders.

## Safety — how it avoids mistakes

- **Copy is the default.** Your originals are never touched unless you choose *Move*.
- **Every copy is verified.** Files are written as `name.partial`, checked with
  SHA-256 against the source, and only then renamed into place. In *Move* mode
  the original is removed only after that check passes.
- **Nothing is ever overwritten.** If a file with the same name already exists:
  identical → skipped ("already in library"); different → saved as `IMG_0001 (1).HEIC`.
  Running the backup again is safe and only copies new photos.
- **Live Photos stay together.** The `.MOV` part of a Live Photo goes into the same
  folder as its photo.
- **Deletes are reversible.** Deleted files go to the Windows **Recycle Bin**, or
  (setting) to `<library>\_PhotoBackup\Deleted`. You always get a confirmation first.
- **Duplicates:** the suggested copy to keep is never pre-selected, and you are
  warned if you select every copy in a group. **Low quality:** nothing is pre-selected.
- **Every run is logged** to `<library>\_PhotoBackup\logs\sort-<date>.csv`
  (source, destination, date and where the date came from).
- The app only accepts connections from your own PC and can only read/delete
  files inside the library folder.

## Install & run (Windows)

1. Install **Python 3.9 or newer** from <https://www.python.org/downloads/>
   (tick **"Add python.exe to PATH"** during install).
2. Download this repository (green **Code** button → *Download ZIP*) and unzip it.
3. Double-click **`start.bat`**. The first run sets everything up (about a minute);
   then your browser opens the app. Keep the black window open while you use it.

Manual start (any OS): `pip install -r requirements.txt` then `python run.py`.

## Getting the photos off the iPhone

Windows does not show an iPhone as a normal drive, so first get the files into a
folder on the PC, then point the app at that folder (the **source**):

- **USB cable:** unlock the iPhone, tap *Trust*, then in File Explorer open
  *This PC → Apple iPhone → Internal Storage* and copy the **DCIM** folder to e.g.
  `D:\iPhone Import`. Or use the **Windows Photos** app → *Import* into a folder.
- **iCloud for Windows:** use the folder it downloads your photos into.

Tip: on the iPhone, *Settings → Photos → Transfer to Mac or PC → **Keep Originals***
gives you the original HEIC/HEVC files with all metadata.

The source folder and the library folder must be different and must not be inside
each other.

## How dates are decided

1. Photos: EXIF `DateTimeOriginal` (the moment the photo was taken).
2. Videos: the creation time stored inside the MOV/MP4, converted to your PC's time zone.
3. Live Photo videos: the date of the matching photo.
4. Anything without a date inside it (some screenshots, downloaded images): the
   file's modified time. The preview shows how many files used each method.

## Tuning duplicates & quality

- **Similarity** slider (0–12): 0 = practically identical images only; higher
  values also group near-identical shots such as bursts. Default 5.
- **Blur below** (default 60): sharpness score under which a photo counts as blurry.
  Raise it to catch slightly soft photos; lower it if sharp photos are flagged.
- **Darker / brighter than** (0–255 average brightness), **smaller than** (megapixels).

Scan results are cached, so re-scanning a large library is fast.

## Development

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Settings, the analysis cache and thumbnails are stored in `%LOCALAPPDATA%\PhotoBackup`.
