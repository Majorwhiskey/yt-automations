"""Transcript extraction with VTT parsing and Whisper fallback."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from config import WHISPER_DEFAULT_LANGUAGE, WHISPER_MODEL

logger = logging.getLogger(__name__)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    segments: list[TranscriptSegment]
    language: str
    source: str  # "captions" | "auto_captions" | "whisper"

    @property
    def has_word_timestamps(self) -> bool:
        return any(seg.words for seg in self.segments)

    @property
    def full_text(self) -> str:
        return " ".join(seg.text for seg in self.segments)


def get_transcript(
    video_metadata,
    language: str = WHISPER_DEFAULT_LANGUAGE,
) -> Transcript | None:
    """Try captions first, then Whisper. Returns None if both fail."""
    if video_metadata.subtitle_path:
        try:
            segments = _parse_vtt(video_metadata.subtitle_path)
            if segments:
                logger.info("Loaded %d manual caption segments", len(segments))
                return Transcript(segments=segments, language=language, source="captions")
        except Exception as exc:
            logger.warning("Failed to parse manual captions: %s", exc)

    if video_metadata.auto_subtitle_path:
        try:
            segments = _parse_vtt(video_metadata.auto_subtitle_path)
            if segments:
                logger.info("Loaded %d auto-caption segments", len(segments))
                return Transcript(
                    segments=segments, language=language, source="auto_captions"
                )
        except Exception as exc:
            logger.warning("Failed to parse auto captions: %s", exc)

    try:
        return _transcribe_with_whisper(video_metadata.audio_path, language)
    except Exception as exc:
        logger.error("Whisper transcription failed: %s", exc)
        return None


_VTT_TIMESTAMP = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)


def _parse_vtt(path: Path) -> list[TranscriptSegment]:
    """Minimal WebVTT parser. Strips cue-styling tags and dedupes consecutive lines.

    YouTube auto-captions emit overlapping rolling captions where each cue repeats
    the previous line plus a new word. We collapse those into clean per-cue text.
    """
    raw = path.read_text(encoding="utf-8", errors="ignore")
    segments: list[TranscriptSegment] = []
    seen_lines: list[str] = []

    blocks = re.split(r"\n\n+", raw)
    for block in blocks:
        block_lines = block.split("\n")
        timestamp_idx = next(
            (i for i, line in enumerate(block_lines) if _VTT_TIMESTAMP.search(line)),
            None,
        )
        if timestamp_idx is None:
            continue
        m = _VTT_TIMESTAMP.search(block_lines[timestamp_idx])
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000

        text_lines = block_lines[timestamp_idx + 1 :]
        text = " ".join(line.strip() for line in text_lines if line.strip())
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()

        if not text:
            continue

        # Deduplicate against the previous line for rolling YouTube auto-captions
        if seen_lines and text in seen_lines[-1]:
            continue
        # If this cue extends the previous one, keep only the new tail
        if seen_lines and seen_lines[-1] in text:
            text = text[len(seen_lines[-1]) :].strip()
            if not text:
                continue
        seen_lines.append(text)

        segments.append(TranscriptSegment(start=start, end=end, text=text))

    return segments


def _transcribe_with_whisper(audio_path: Path, language: str) -> Transcript:
    """Run Whisper with word-level timestamps. Imported lazily — Whisper pulls in torch."""
    import whisper

    logger.info("Loading Whisper model '%s'", WHISPER_MODEL)
    model = whisper.load_model(WHISPER_MODEL)

    logger.info("Transcribing %s", audio_path)
    result = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        verbose=False,
    )

    segments: list[TranscriptSegment] = []
    for seg in result.get("segments", []):
        words = [
            Word(
                start=float(w["start"]),
                end=float(w["end"]),
                text=str(w["word"]).strip(),
            )
            for w in seg.get("words", [])
            if w.get("start") is not None and w.get("end") is not None
        ]
        segments.append(
            TranscriptSegment(
                start=float(seg["start"]),
                end=float(seg["end"]),
                text=str(seg["text"]).strip(),
                words=words,
            )
        )

    return Transcript(
        segments=segments,
        language=result.get("language", language),
        source="whisper",
    )
