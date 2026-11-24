"""Extract and cache mono WAV audio via ffmpeg."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from app.media.probe import require_ffmpeg_tools


DEFAULT_SAMPLE_RATE = 22050


def _cache_dir() -> Path:
    root = Path(tempfile.gettempdir()) / "yt-multi-video-match-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_path_for(media_path: Path, sample_rate: int) -> Path:
    media_path = Path(media_path).resolve()
    stat = media_path.stat()
    key = f"{media_path}|{stat.st_mtime_ns}|{stat.st_size}|{sample_rate}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    safe_name = media_path.stem[:40].replace(" ", "_")
    return _cache_dir() / f"{safe_name}_{digest}_{sample_rate}.wav"


def extract_mono_wav(
    media_path: Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    *,
    force: bool = False,
) -> Path:
    """Extract mono PCM WAV; returns path to cached file."""
    ffmpeg, _ = require_ffmpeg_tools()
    media_path = Path(media_path).resolve()
    out = cache_path_for(media_path, sample_rate)
    if out.exists() and not force:
        return out

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(
            f"ffmpeg audio extract failed for {media_path}: {result.stderr[-800:]}"
        )
    return out


def load_mono_audio(
    media_path: Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> tuple[np.ndarray, int]:
    """Return float32 mono samples and sample rate."""
    wav = extract_mono_wav(media_path, sample_rate=sample_rate)
    audio, sr = sf.read(str(wav), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1).astype(np.float32)
    if sr != sample_rate:
        raise RuntimeError(f"Unexpected sample rate {sr}, expected {sample_rate}")
    return audio, sr
