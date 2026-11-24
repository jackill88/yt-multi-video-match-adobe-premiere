"""Export a Premiere-importable FCP7 XML sequence."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote
from xml.dom import minidom

from app.media.probe import MediaInfo, probe_media
from app.sync.match import MatchResult
from app.sync.timeline import MainTimeline


@dataclass
class ExportOptions:
    sequence_name: str = "Concert Sync"
    include_low_confidence: bool = False
    include_failed: bool = False


def _frames(seconds: float, timebase: Fraction) -> int:
    return max(0, int(round(seconds * float(timebase))))


def _pathurl(path: Path) -> str:
    """Windows-friendly file URL for Premiere."""
    resolved = Path(path).resolve().as_posix()
    if len(resolved) >= 2 and resolved[1] == ":":
        drive = resolved[0].upper()
        rest = resolved[2:]
        encoded = quote(rest, safe="/")
        return f"file://localhost/{drive}:{encoded}"
    return "file://localhost" + quote(resolved, safe="/")


def _rate_elem(parent: ET.Element, timebase: Fraction) -> None:
    rate = ET.SubElement(parent, "rate")
    tb = ET.SubElement(rate, "timebase")
    ntsc = False
    if abs(float(timebase) - 29.97) < 0.02 or timebase == Fraction(30000, 1001):
        tb_int = 30
        ntsc = True
    elif abs(float(timebase) - 23.976) < 0.02 or timebase == Fraction(24000, 1001):
        tb_int = 24
        ntsc = True
    elif abs(float(timebase) - 59.94) < 0.02 or timebase == Fraction(60000, 1001):
        tb_int = 60
        ntsc = True
    else:
        tb_int = max(1, int(round(float(timebase))))
    tb.text = str(tb_int)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _choose_sequence_rate(timeline: MainTimeline) -> Fraction:
    if not timeline.clips:
        return Fraction(25, 1)
    return timeline.clips[0].info.timebase


def _file_id(path: Path, counter: dict[str, int]) -> str:
    key = str(path.resolve()).lower()
    if key not in counter:
        counter[key] = len(counter) + 1
    return f"file-{counter[key]}"


def _fill_file_element(file_el: ET.Element, info: MediaInfo, timebase: Fraction) -> None:
    duration_frames = max(1, _frames(info.duration_sec, timebase))
    ET.SubElement(file_el, "name").text = info.path.name
    ET.SubElement(file_el, "pathurl").text = _pathurl(info.path)
    _rate_elem(file_el, timebase)
    ET.SubElement(file_el, "duration").text = str(duration_frames)
    media = ET.SubElement(file_el, "media")
    if info.has_video:
        v = ET.SubElement(media, "video")
        sc = ET.SubElement(v, "samplecharacteristics")
        _rate_elem(sc, timebase)
        ET.SubElement(sc, "width").text = str(info.width or 1920)
        ET.SubElement(sc, "height").text = str(info.height or 1080)
        ET.SubElement(sc, "anamorphic").text = "FALSE"
        ET.SubElement(sc, "pixelaspectratio").text = "square"
        ET.SubElement(sc, "fielddominance").text = "none"
    if info.has_audio:
        a = ET.SubElement(media, "audio")
        sc = ET.SubElement(a, "samplecharacteristics")
        ET.SubElement(sc, "depth").text = "16"
        ET.SubElement(sc, "samplerate").text = str(info.sample_rate or 48000)
        ET.SubElement(a, "channelcount").text = str(max(1, info.audio_channels or 2))


def _add_time_remap(clipitem: ET.Element, *, speed: float, mediatype: str) -> None:
    """Premiere-compatible constant speed via Time Remap filter."""
    speed_pct = max(0.01, speed * 100.0)
    filt = ET.SubElement(clipitem, "filter")
    effect = ET.SubElement(filt, "effect")
    ET.SubElement(effect, "name").text = "Time Remap"
    ET.SubElement(effect, "effectid").text = "timeremap"
    ET.SubElement(effect, "effectcategory").text = "motion"
    ET.SubElement(effect, "effecttype").text = "motion"
    ET.SubElement(effect, "mediatype").text = mediatype

    p_var = ET.SubElement(effect, "parameter", authoringApp="PremierePro")
    ET.SubElement(p_var, "parameterid").text = "variablespeed"
    ET.SubElement(p_var, "name").text = "variablespeed"
    ET.SubElement(p_var, "valuemin").text = "0"
    ET.SubElement(p_var, "valuemax").text = "1"
    ET.SubElement(p_var, "value").text = "0"

    p_speed = ET.SubElement(effect, "parameter", authoringApp="PremierePro")
    ET.SubElement(p_speed, "parameterid").text = "speed"
    ET.SubElement(p_speed, "name").text = "speed"
    ET.SubElement(p_speed, "valuemin").text = "-100000"
    ET.SubElement(p_speed, "valuemax").text = "100000"
    ET.SubElement(p_speed, "value").text = f"{speed_pct:.6f}"

    p_rev = ET.SubElement(effect, "parameter", authoringApp="PremierePro")
    ET.SubElement(p_rev, "parameterid").text = "reverse"
    ET.SubElement(p_rev, "name").text = "reverse"
    ET.SubElement(p_rev, "value").text = "FALSE"

    p_blend = ET.SubElement(effect, "parameter", authoringApp="PremierePro")
    ET.SubElement(p_blend, "parameterid").text = "frameblending"
    ET.SubElement(p_blend, "name").text = "frameblending"
    ET.SubElement(p_blend, "value").text = "FALSE"


def _add_clipitem(
    track: ET.Element,
    *,
    clip_id: str,
    name: str,
    info: MediaInfo,
    timeline_start_sec: float,
    timebase: Fraction,
    file_ids: dict[str, int],
    written_files: set[str],
    speed: float = 1.0,
    mediatype: str = "video",
) -> None:
    source_frames = max(1, _frames(info.duration_sec, timebase))
    # speed = source_duration / timeline_duration  => timeline = source / speed
    safe_speed = speed if speed > 1e-6 else 1.0
    timeline_dur_sec = info.duration_sec / safe_speed
    timeline_frames = max(1, _frames(timeline_dur_sec, timebase))
    start_f = _frames(timeline_start_sec, timebase)
    end_f = start_f + timeline_frames

    clipitem = ET.SubElement(track, "clipitem", id=clip_id)
    ET.SubElement(clipitem, "name").text = name
    ET.SubElement(clipitem, "enabled").text = "TRUE"
    ET.SubElement(clipitem, "duration").text = str(source_frames)
    _rate_elem(clipitem, timebase)
    ET.SubElement(clipitem, "start").text = str(start_f)
    ET.SubElement(clipitem, "end").text = str(end_f)
    ET.SubElement(clipitem, "in").text = "0"
    ET.SubElement(clipitem, "out").text = str(source_frames)

    fid = _file_id(info.path, file_ids)
    file_el = ET.SubElement(clipitem, "file", id=fid)
    if fid not in written_files:
        written_files.add(fid)
        _fill_file_element(file_el, info, timebase)

    if abs(safe_speed - 1.0) > 1e-6:
        _add_time_remap(clipitem, speed=safe_speed, mediatype=mediatype)


def _should_include(result: MatchResult, options: ExportOptions) -> bool:
    if result.status == "OK":
        return True
    if result.status == "Low" and options.include_low_confidence:
        return True
    if result.status == "Failed" and options.include_failed:
        return True
    return False


def build_fcp7_xml(
    timeline: MainTimeline,
    matches: list[MatchResult],
    options: ExportOptions | None = None,
) -> str:
    options = options or ExportOptions()
    timebase = _choose_sequence_rate(timeline)
    seq_duration = _frames(timeline.duration_sec, timebase)
    file_ids: dict[str, int] = {}
    written_files: set[str] = set()

    info_cache: dict[Path, MediaInfo] = {clip.path: clip.info for clip in timeline.clips}

    xmeml = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(xmeml, "sequence", id="sequence-1")
    ET.SubElement(sequence, "name").text = options.sequence_name
    ET.SubElement(sequence, "duration").text = str(seq_duration)
    _rate_elem(sequence, timebase)

    timecode = ET.SubElement(sequence, "timecode")
    _rate_elem(timecode, timebase)
    ET.SubElement(timecode, "string").text = "00:00:00:00"
    ET.SubElement(timecode, "frame").text = "0"
    ET.SubElement(timecode, "displayformat").text = "NDF"

    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    _rate_elem(sc, timebase)
    first = timeline.clips[0].info if timeline.clips else None
    ET.SubElement(sc, "width").text = str((first.width if first else 0) or 1920)
    ET.SubElement(sc, "height").text = str((first.height if first else 0) or 1080)
    ET.SubElement(sc, "anamorphic").text = "FALSE"
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    ET.SubElement(sc, "fielddominance").text = "none"

    audio = ET.SubElement(media, "audio")

    v_main = ET.SubElement(video, "track")
    a_main = ET.SubElement(audio, "track")
    ET.SubElement(a_main, "enabled").text = "TRUE"
    ET.SubElement(a_main, "locked").text = "FALSE"

    clip_counter = 1
    for clip in timeline.clips:
        _add_clipitem(
            v_main,
            clip_id=f"clip-v-{clip_counter}",
            name=clip.path.name,
            info=clip.info,
            timeline_start_sec=clip.start_sec,
            timebase=timebase,
            file_ids=file_ids,
            written_files=written_files,
            mediatype="video",
        )
        if clip.info.has_audio:
            _add_clipitem(
                a_main,
                clip_id=f"clip-a-{clip_counter}",
                name=clip.path.name,
                info=clip.info,
                timeline_start_sec=clip.start_sec,
                timebase=timebase,
                file_ids=file_ids,
                written_files=written_files,
                mediatype="audio",
            )
        clip_counter += 1

    cameras: list[str] = []
    by_camera: dict[str, list[MatchResult]] = {}
    for m in matches:
        if not _should_include(m, options):
            continue
        if m.camera not in by_camera:
            by_camera[m.camera] = []
            cameras.append(m.camera)
        by_camera[m.camera].append(m)

    for cam in cameras:
        v_track = ET.SubElement(video, "track")
        a_track = ET.SubElement(audio, "track")
        ET.SubElement(a_track, "enabled").text = "TRUE"
        ET.SubElement(a_track, "locked").text = "FALSE"

        for m in sorted(by_camera[cam], key=lambda r: r.offset_sec):
            path = Path(m.path).resolve()
            if path not in info_cache:
                info_cache[path] = probe_media(path)
            info = info_cache[path]
            _add_clipitem(
                v_track,
                clip_id=f"clip-v-{clip_counter}",
                name=f"[{cam}] {path.name}",
                info=info,
                timeline_start_sec=m.offset_sec,
                timebase=timebase,
                file_ids=file_ids,
                written_files=written_files,
                speed=getattr(m, "speed", 1.0) or 1.0,
                mediatype="video",
            )
            if info.has_audio:
                _add_clipitem(
                    a_track,
                    clip_id=f"clip-a-{clip_counter}",
                    name=f"[{cam}] {path.name}",
                    info=info,
                    timeline_start_sec=m.offset_sec,
                    timebase=timebase,
                    file_ids=file_ids,
                    written_files=written_files,
                    speed=getattr(m, "speed", 1.0) or 1.0,
                    mediatype="audio",
                )
            clip_counter += 1

    rough = ET.tostring(xmeml, encoding="utf-8")
    parsed = minidom.parseString(rough)
    pretty = parsed.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")
    if pretty.startswith("<?xml"):
        first_nl = pretty.find("\n")
        decl = pretty[:first_nl]
        body = pretty[first_nl + 1 :]
        return f"{decl}\n<!DOCTYPE xmeml>\n{body}"
    return "<!DOCTYPE xmeml>\n" + pretty


def write_fcp7_xml(
    out_path: Path,
    timeline: MainTimeline,
    matches: list[MatchResult],
    options: ExportOptions | None = None,
) -> Path:
    out_path = Path(out_path)
    xml_text = build_fcp7_xml(timeline, matches, options=options)
    out_path.write_text(xml_text, encoding="utf-8")
    return out_path
