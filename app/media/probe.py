"""Probe media files with ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class FFmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg/ffprobe is missing from PATH."""


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration_sec: float
    fps: float
    timebase: Fraction
    width: int
    height: int
    has_video: bool
    has_audio: bool
    audio_channels: int
    sample_rate: int


def require_ffmpeg_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if not ffprobe:
        missing.append("ffprobe")
    if missing:
        raise FFmpegNotFoundError(
            "Missing on PATH: "
            + ", ".join(missing)
            + ". Install with: winget install Gyan.FFmpeg"
        )
    return ffmpeg, ffprobe


def _parse_rate(value: str | None) -> Fraction | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None


def probe_media(path: Path) -> MediaInfo:
    _, ffprobe = require_ffmpeg_tools()
    path = Path(path).resolve()
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    if fmt.get("duration"):
        duration = float(fmt["duration"])
    elif video and video.get("duration"):
        duration = float(video["duration"])
    elif audio and audio.get("duration"):
        duration = float(audio["duration"])

    fps = 25.0
    timebase = Fraction(25, 1)
    width = 0
    height = 0
    if video:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        rate = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(
            video.get("r_frame_rate")
        )
        if rate and rate > 0:
            fps = float(rate)
            # Prefer integer timebases Premiere likes; keep NTSC as-is.
            if abs(fps - round(fps)) < 0.001:
                timebase = Fraction(int(round(fps)), 1)
            else:
                timebase = rate.limit_denominator(1001)

    audio_channels = int(audio.get("channels") or 0) if audio else 0
    sample_rate = int(audio.get("sample_rate") or 0) if audio else 0

    return MediaInfo(
        path=path,
        duration_sec=duration,
        fps=fps,
        timebase=timebase,
        width=width,
        height=height,
        has_video=video is not None,
        has_audio=audio is not None,
        audio_channels=audio_channels,
        sample_rate=sample_rate,
    )


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mxf",
    ".mkv",
    ".avi",
    ".m4v",
    ".mts",
    ".m2ts",
    ".webm",
}


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
