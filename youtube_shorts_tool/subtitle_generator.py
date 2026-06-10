"""Subtitle generation: SRT and karaoke-style ASS."""

from __future__ import annotations

import logging
from pathlib import Path

from config import (
    SUBTITLE_BOTTOM_MARGIN,
    SUBTITLE_FONT,
    SUBTITLE_FONT_SIZE,
    SUBTITLE_HIGHLIGHT_COLOR,
    SUBTITLE_OUTLINE_COLOR,
    SUBTITLE_OUTLINE_WIDTH,
    SUBTITLE_PRIMARY_COLOR,
    VERTICAL_HEIGHT,
    VERTICAL_WIDTH,
)

logger = logging.getLogger(__name__)


def write_srt(
    segments,
    output_path: Path,
    clip_start: float,
    clip_end: float,
) -> Path:
    """Write a standard SRT covering the segments within [clip_start, clip_end]."""
    lines: list[str] = []
    counter = 1

    for seg in segments:
        if seg.end <= clip_start or seg.start >= clip_end:
            continue
        local_start = max(0.0, seg.start - clip_start)
        local_end = min(clip_end - clip_start, seg.end - clip_start)
        if local_end - local_start < 0.05:
            continue

        lines.append(str(counter))
        lines.append(f"{_format_srt_time(local_start)} --> {_format_srt_time(local_end)}")
        lines.append(seg.text.strip())
        lines.append("")
        counter += 1

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def write_karaoke_ass(
    segments,
    output_path: Path,
    clip_start: float,
    clip_end: float,
) -> Path:
    """Write an ASS subtitle file with per-word karaoke highlighting.

    Uses the `\k` ASS tag to highlight each word as it's spoken. Requires word-level
    timestamps from Whisper — falls back to write_srt semantics if no words present.
    """
    has_words = any(getattr(seg, "words", None) for seg in segments)
    if not has_words:
        return write_srt(segments, output_path.with_suffix(".srt"), clip_start, clip_end)

    header = _ass_header()
    events: list[str] = []

    for seg in segments:
        if seg.end <= clip_start or seg.start >= clip_end:
            continue
        words = [w for w in seg.words if w.end > clip_start and w.start < clip_end]
        if not words:
            continue

        local_start = max(0.0, words[0].start - clip_start)
        local_end = min(clip_end - clip_start, words[-1].end - clip_start)

        karaoke = _build_karaoke_line(words, clip_start, clip_end)

        events.append(
            f"Dialogue: 0,{_format_ass_time(local_start)},{_format_ass_time(local_end)},"
            f"Karaoke,,0,0,0,,{karaoke}"
        )

    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return output_path


def _build_karaoke_line(words, clip_start: float, clip_end: float) -> str:
    parts: list[str] = []
    for w in words:
        w_start = max(0.0, w.start - clip_start)
        w_end = min(clip_end - clip_start, w.end - clip_start)
        duration_cs = max(1, int((w_end - w_start) * 100))
        text = w.text.replace("{", "(").replace("}", ")")
        parts.append(f"{{\\kf{duration_cs}}}{text} ")
    return "".join(parts).rstrip()


def _ass_header() -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{SUBTITLE_FONT},{SUBTITLE_FONT_SIZE},{SUBTITLE_PRIMARY_COLOR},{SUBTITLE_HIGHLIGHT_COLOR},{SUBTITLE_OUTLINE_COLOR},&H00000000,-1,0,0,0,100,100,0,0,1,{SUBTITLE_OUTLINE_WIDTH},0,2,40,40,{SUBTITLE_BOTTOM_MARGIN},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"
