"""Build an ordered Main-camera timeline with cumulative offsets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.media.probe import MediaInfo, probe_media


@dataclass
class TimelineClip:
    path: Path
    info: MediaInfo
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


@dataclass
class MainTimeline:
    clips: list[TimelineClip]

    @property
    def duration_sec(self) -> float:
        if not self.clips:
            return 0.0
        return self.clips[-1].end_sec

    def clip_at(self, timeline_sec: float) -> TimelineClip | None:
        for clip in self.clips:
            if clip.start_sec <= timeline_sec < clip.end_sec:
                return clip
        if self.clips and abs(timeline_sec - self.clips[-1].end_sec) < 1e-6:
            return self.clips[-1]
        return None


def build_main_timeline(paths: list[Path]) -> MainTimeline:
    clips: list[TimelineClip] = []
    cursor = 0.0
    for path in paths:
        info = probe_media(path)
        if info.duration_sec <= 0:
            raise RuntimeError(f"Could not determine duration for {path}")
        start = cursor
        end = cursor + info.duration_sec
        clips.append(
            TimelineClip(path=Path(path).resolve(), info=info, start_sec=start, end_sec=end)
        )
        cursor = end
    return MainTimeline(clips=clips)
