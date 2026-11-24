"""Chroma-based audio matching of angle clips into a Main timeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import librosa
import numpy as np
from scipy.signal import correlate

from app.media.extract import DEFAULT_SAMPLE_RATE, load_mono_audio
from app.sync.timeline import MainTimeline, build_main_timeline


ProgressCallback = Callable[[str, float], None]


@dataclass
class MatchResult:
    path: Path
    camera: str
    offset_sec: float
    confidence: float
    status: str  # OK | Low | Failed
    matched_main_clip: Path | None
    message: str = ""
    # Playback speed relative to Main (1.0 = no drift). Premiere speed% = speed * 100.
    speed: float = 1.0
    drift_ppm: float = 0.0
    anchor_count: int = 0


@dataclass
class MatchSettings:
    sample_rate: int = DEFAULT_SAMPLE_RATE
    hop_length: int = 512
    n_fft: int = 2048
    confidence_threshold: float = 1.8
    min_query_sec: float = 3.0
    correct_drift: bool = False
    drift_window_sec: float = 20.0
    drift_anchors: int = 5
    # Reject absurd clock ratios (typical consumer drift is << 1%).
    max_abs_drift_ppm: float = 5000.0
    drift_search_pad_sec: float = 8.0


def _chromagram(audio: np.ndarray, sr: int, hop_length: int, n_fft: int) -> np.ndarray:
    if audio.size == 0:
        return np.zeros((12, 0), dtype=np.float32)
    chroma = librosa.feature.chroma_stft(
        y=audio.astype(np.float32),
        sr=sr,
        hop_length=hop_length,
        n_fft=n_fft,
        n_chroma=12,
    )
    # Normalize each frame for mic/EQ robustness.
    norms = np.linalg.norm(chroma, axis=0, keepdims=True)
    norms = np.maximum(norms, 1e-8)
    return (chroma / norms).astype(np.float32)


def _confidence_and_lag(ref: np.ndarray, query: np.ndarray) -> tuple[int, float]:
    """Return lag (in frames, query start relative to ref) and confidence."""
    if ref.shape[1] == 0 or query.shape[1] == 0:
        return 0, 0.0
    if query.shape[1] > ref.shape[1]:
        query = query[:, : ref.shape[1]]

    corr_sum = None
    for i in range(ref.shape[0]):
        c = correlate(ref[i], query[i], mode="valid", method="fft")
        corr_sum = c if corr_sum is None else corr_sum + c
    assert corr_sum is not None
    corr = corr_sum / ref.shape[0]

    if corr.size == 0:
        return 0, 0.0

    peak_idx = int(np.argmax(corr))
    peak = float(corr[peak_idx])
    median = float(np.median(corr))
    mad = float(np.median(np.abs(corr - median))) + 1e-8
    confidence = (peak - median) / mad
    return peak_idx, confidence


def _status_for(confidence: float, threshold: float, ok: bool) -> str:
    if not ok:
        return "Failed"
    if confidence >= threshold:
        return "OK"
    return "Low"


def build_main_chroma(
    timeline: MainTimeline,
    settings: MatchSettings,
    progress: ProgressCallback | None = None,
) -> tuple[np.ndarray, float]:
    """Return concatenated Main chromagram and seconds-per-frame."""
    pieces: list[np.ndarray] = []
    total = max(len(timeline.clips), 1)
    for i, clip in enumerate(timeline.clips):
        if progress:
            progress(f"Extracting Main audio: {clip.path.name}", i / (total + 1))
        audio, sr = load_mono_audio(clip.path, sample_rate=settings.sample_rate)
        chroma = _chromagram(audio, sr, settings.hop_length, settings.n_fft)
        pieces.append(chroma)

    if not pieces:
        return np.zeros((12, 0), dtype=np.float32), settings.hop_length / settings.sample_rate

    main_chroma = np.concatenate(pieces, axis=1)
    sec_per_frame = settings.hop_length / float(settings.sample_rate)
    return main_chroma, sec_per_frame


def _failed(
    path: Path,
    camera: str,
    message: str,
) -> MatchResult:
    return MatchResult(
        path=path,
        camera=camera,
        offset_sec=0.0,
        confidence=0.0,
        status="Failed",
        matched_main_clip=None,
        message=message,
    )


def _match_window(
    main_chroma: np.ndarray,
    query_chroma: np.ndarray,
    sec_per_frame: float,
    *,
    search_start_sec: float | None = None,
    search_end_sec: float | None = None,
) -> tuple[float, float]:
    """Match query against Main (optionally a slice). Returns (timeline_sec, confidence)."""
    if query_chroma.shape[1] < 4 or main_chroma.shape[1] < 4:
        return 0.0, 0.0

    ref = main_chroma
    ref_offset_sec = 0.0
    if search_start_sec is not None or search_end_sec is not None:
        start_f = 0
        end_f = main_chroma.shape[1]
        if search_start_sec is not None:
            start_f = max(0, int(search_start_sec / sec_per_frame))
        if search_end_sec is not None:
            end_f = min(main_chroma.shape[1], int(np.ceil(search_end_sec / sec_per_frame)))
        # Need room for the query.
        if end_f - start_f < query_chroma.shape[1] + 2:
            start_f = max(0, end_f - query_chroma.shape[1] - 2)
        if end_f <= start_f:
            return 0.0, 0.0
        ref = main_chroma[:, start_f:end_f]
        ref_offset_sec = start_f * sec_per_frame

    lag_frames, confidence = _confidence_and_lag(ref, query_chroma)
    return ref_offset_sec + lag_frames * sec_per_frame, confidence


def _fit_drift(
    src_times: np.ndarray,
    timeline_times: np.ndarray,
) -> tuple[float, float, float]:
    """
    Fit timeline = offset + rate * src.

    Returns (offset_sec, rate, residual_std).
    rate = d(timeline)/d(src). Playback speed = 1/rate.
    """
    if src_times.size < 2:
        return float(timeline_times[0]) if timeline_times.size else 0.0, 1.0, 0.0

    # Least squares with simple outlier rejection (drop worst residual once).
    A = np.vstack([np.ones(src_times.size), src_times]).T
    coef, _, _, _ = np.linalg.lstsq(A, timeline_times, rcond=None)
    offset, rate = float(coef[0]), float(coef[1])
    pred = offset + rate * src_times
    resid = timeline_times - pred
    if src_times.size >= 4:
        worst = int(np.argmax(np.abs(resid)))
        mask = np.ones(src_times.size, dtype=bool)
        mask[worst] = False
        coef, _, _, _ = np.linalg.lstsq(A[mask], timeline_times[mask], rcond=None)
        offset, rate = float(coef[0]), float(coef[1])
        pred = offset + rate * src_times[mask]
        resid = timeline_times[mask] - pred
    resid_std = float(np.std(resid)) if resid.size else 0.0
    return offset, rate, resid_std


def _match_with_drift(
    clip_path: Path,
    camera: str,
    timeline: MainTimeline,
    main_chroma: np.ndarray,
    query_chroma: np.ndarray,
    duration: float,
    sec_per_frame: float,
    settings: MatchSettings,
) -> MatchResult:
    window_sec = min(settings.drift_window_sec, max(settings.min_query_sec, duration / 3.0))
    window_frames = max(4, int(window_sec / sec_per_frame))
    if query_chroma.shape[1] < window_frames + 2:
        # Fall back to constant-offset match.
        return _match_constant(
            clip_path, camera, timeline, main_chroma, query_chroma, duration, sec_per_frame, settings
        )

    # Coarse offset from the first window.
    first = query_chroma[:, :window_frames]
    coarse_offset, coarse_conf = _match_window(main_chroma, first, sec_per_frame)
    if coarse_conf < 1.0:
        return _failed(clip_path, camera, "Could not lock coarse sync for drift fit")

    n = max(2, int(settings.drift_anchors))
    # Anchor centers in source time.
    half = window_sec / 2.0
    lo = half
    hi = max(lo, duration - half)
    centers = np.linspace(lo, hi, n)

    src_times: list[float] = []
    tl_times: list[float] = []
    confs: list[float] = []

    for i, center in enumerate(centers):
        # Provisional rate=1 for search window; refine after 2+ points.
        if len(src_times) >= 2:
            offset_est, rate_est, _ = _fit_drift(np.array(src_times), np.array(tl_times))
            expected = offset_est + rate_est * float(center)
        else:
            expected = coarse_offset + float(center)

        start_src = float(center) - half
        start_f = max(0, int(start_src / sec_per_frame))
        end_f = min(query_chroma.shape[1], start_f + window_frames)
        if end_f - start_f < 4:
            continue
        q = query_chroma[:, start_f:end_f]
        pad = settings.drift_search_pad_sec + abs(float(center) - centers[0]) * 0.02
        tl, conf = _match_window(
            main_chroma,
            q,
            sec_per_frame,
            search_start_sec=expected - half - pad,
            search_end_sec=expected + half + pad + window_sec,
        )
        if conf < 1.0:
            continue
        src_times.append(start_f * sec_per_frame)
        tl_times.append(tl)
        confs.append(conf)

    if len(src_times) < 2:
        # Drift fit failed — keep coarse constant offset.
        result = _match_constant(
            clip_path, camera, timeline, main_chroma, query_chroma, duration, sec_per_frame, settings
        )
        result.message = (result.message + "; " if result.message else "") + "drift fit fallback"
        return result

    offset, rate, resid_std = _fit_drift(np.array(src_times), np.array(tl_times))
    if rate <= 0:
        return _failed(clip_path, camera, "Invalid drift rate")

    speed = 1.0 / rate
    drift_ppm = (rate - 1.0) * 1_000_000.0
    if abs(drift_ppm) > settings.max_abs_drift_ppm:
        result = _match_constant(
            clip_path, camera, timeline, main_chroma, query_chroma, duration, sec_per_frame, settings
        )
        result.message = f"drift {drift_ppm:.0f} ppm out of range; used constant offset"
        return result

    offset = max(0.0, offset)
    confidence = float(np.median(confs))
    # Penalize noisy fits slightly.
    if resid_std > 0.25:
        confidence *= 0.85

    matched = timeline.clip_at(offset)
    ok = confidence >= 1.0 and matched is not None
    status = _status_for(confidence, settings.confidence_threshold, ok)
    msg = f"drift {drift_ppm:+.0f} ppm, speed {speed:.6f}x, anchors {len(src_times)}"
    if not ok:
        msg = "Weak or ambiguous match; " + msg

    return MatchResult(
        path=clip_path,
        camera=camera,
        offset_sec=offset,
        confidence=confidence,
        status=status,
        matched_main_clip=matched.path if matched else None,
        message=msg,
        speed=float(speed),
        drift_ppm=float(drift_ppm),
        anchor_count=len(src_times),
    )


def _match_constant(
    clip_path: Path,
    camera: str,
    timeline: MainTimeline,
    main_chroma: np.ndarray,
    query_chroma: np.ndarray,
    duration: float,
    sec_per_frame: float,
    settings: MatchSettings,
) -> MatchResult:
    lag_frames, confidence = _confidence_and_lag(main_chroma, query_chroma)
    offset_sec = max(0.0, lag_frames * sec_per_frame)
    if offset_sec > timeline.duration_sec:
        offset_sec = max(0.0, timeline.duration_sec - duration)

    matched = timeline.clip_at(offset_sec)
    ok = confidence >= 1.0 and matched is not None
    status = _status_for(confidence, settings.confidence_threshold, ok)

    return MatchResult(
        path=clip_path,
        camera=camera,
        offset_sec=offset_sec,
        confidence=float(confidence),
        status=status,
        matched_main_clip=matched.path if matched else None,
        message="" if ok else "Weak or ambiguous match",
        speed=1.0,
        drift_ppm=0.0,
        anchor_count=1,
    )


def match_clip_to_main(
    clip_path: Path,
    camera: str,
    timeline: MainTimeline,
    main_chroma: np.ndarray,
    sec_per_frame: float,
    settings: MatchSettings,
) -> MatchResult:
    clip_path = Path(clip_path).resolve()
    try:
        audio, sr = load_mono_audio(clip_path, sample_rate=settings.sample_rate)
    except Exception as exc:  # noqa: BLE001 - surface to UI
        return _failed(clip_path, camera, str(exc))

    duration = audio.size / float(sr)
    if duration < settings.min_query_sec:
        return _failed(clip_path, camera, f"Clip too short ({duration:.1f}s)")

    query = _chromagram(audio, sr, settings.hop_length, settings.n_fft)
    if main_chroma.shape[1] < 4 or query.shape[1] < 4:
        return _failed(clip_path, camera, "Not enough audio features")

    if settings.correct_drift and duration >= settings.drift_window_sec * 1.5:
        return _match_with_drift(
            clip_path,
            camera,
            timeline,
            main_chroma,
            query,
            duration,
            sec_per_frame,
            settings,
        )
    return _match_constant(
        clip_path,
        camera,
        timeline,
        main_chroma,
        query,
        duration,
        sec_per_frame,
        settings,
    )


def match_all(
    main_paths: list[Path],
    other_items: list[tuple[Path, str]],
    settings: MatchSettings | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[MainTimeline, list[MatchResult]]:
    """
    Match other clips into the Main timeline.

    other_items: list of (path, camera_label)
    """
    settings = settings or MatchSettings()
    if progress:
        progress("Probing Main clips…", 0.02)
    timeline = build_main_timeline(main_paths)
    if not timeline.clips:
        raise RuntimeError("No Main clips provided")

    main_chroma, sec_per_frame = build_main_chroma(timeline, settings, progress=progress)

    results: list[MatchResult] = []
    total = max(len(other_items), 1)
    for i, (path, camera) in enumerate(other_items):
        if progress:
            progress(f"Matching: {Path(path).name}", 0.2 + 0.8 * (i / total))
        results.append(
            match_clip_to_main(
                Path(path),
                camera,
                timeline,
                main_chroma,
                sec_per_frame,
                settings,
            )
        )

    if progress:
        progress("Matching complete", 1.0)
    return timeline, results
