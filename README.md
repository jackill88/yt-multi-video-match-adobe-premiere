# Concert Multi-Cam Audio Sync (Premiere XML)

Windows Python GUI that matches concert camera clips to an ordered **Main** timeline by audio, then exports an **FCP7 XML** sequence Adobe Premiere can import.

## Requirements

- Windows 10/11
- Python 3.11+
- **ffmpeg** and **ffprobe** on PATH

### Install ffmpeg (pick one)

```bat
winget install Gyan.FFmpeg
```

or

```bat
choco install ffmpeg
```

Confirm:

```bat
ffmpeg -version
ffprobe -version
```

## Setup

```bat
python -m pip install -r requirements.txt
```

Or double-click `run.bat` (installs deps if needed, then launches the app).

## Build Windows exe (PyInstaller)

Prefer a clean virtualenv (smaller/faster than a full Anaconda env):

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-build.txt
build.bat
```

Output folder: `dist\ConcertSync\` — run `ConcertSync.exe` from that folder (keep the whole folder together; do not move only the `.exe`).

The packaged app still needs **ffmpeg** / **ffprobe** on PATH (not bundled).

Manual equivalent:

```bat
python -m PyInstaller --noconfirm --clean concert_sync.spec
```

## Usage

1. Add **Main** clips and order them (top → bottom = show timeline).
2. Add other camera files or folders (folder name becomes the track/camera label).
3. Optionally enable **Correct drift** (for Cam 2/3 clock or sample-rate mismatch).
4. Click **Match** and review confidence, speed, and drift ppm (OK / Low / Failed).
5. Click **Export XML…** and save the sequence.
6. In Premiere: **File → Import** the XML.

Main clips land on V1/A1 in order. Matched angles are placed on later tracks at the computed offsets. Drift correction writes a Premiere Time Remap speed so audio stays locked over long takes.

## Notes

- Matching uses chromagram correlation (tolerant of different mics/EQ).
- **Correct drift** samples several audio anchors along each angle clip, fits offset + linear rate, and exports speed ≠ 100% when needed.
- Short clips fall back to constant offset only.
- Low-confidence matches are skipped on export unless you check **Include low-confidence matches**.
