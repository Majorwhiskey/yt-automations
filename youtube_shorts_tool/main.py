"""CLI entry point — orchestrates the full Shorts generation pipeline."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from clip_selector import (
    SelectedClip,
    adjust_to_sentence_boundary,
    generate_publish_metadata,
    select_clips,
)
from config import (
    DEFAULT_MAX_CLIP_DURATION,
    DEFAULT_MAX_CLIPS,
    DEFAULT_MIN_CLIP_DURATION,
    LOG_FILE,
    MIN_VALID_CLIP_BYTES,
    OUTPUT_DIR,
    WHISPER_DEFAULT_LANGUAGE,
)
from downloader import download_video
from metadata_writer import write_clip_metadata, write_shorts_report
from processor import cut_clip, process_to_vertical
from subtitle_generator import write_karaoke_ass, write_srt
from transcriber import get_transcript

logger = logging.getLogger(__name__)


def setup_logging(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / LOG_FILE
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate polished YouTube Shorts from any YouTube video URL.",
    )
    parser.add_argument("--url", required=True, help="Source YouTube URL")
    parser.add_argument("--max-clips", type=int, default=DEFAULT_MAX_CLIPS)
    parser.add_argument("--min-duration", type=int, default=DEFAULT_MIN_CLIP_DURATION)
    parser.add_argument("--max-duration", type=int, default=DEFAULT_MAX_CLIP_DURATION)
    parser.add_argument("--language", default=WHISPER_DEFAULT_LANGUAGE)
    parser.add_argument("--no-subtitles", action="store_true",
                        help="Skip burning subtitles onto the final video")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR,
                        help="Output root directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not shutil.which("claude"):
        print(
            "ERROR: `claude` CLI not found on PATH. Install Claude Code "
            "(https://claude.com/claude-code) and run `claude login`.",
            file=sys.stderr,
        )
        return 1

    args.output.mkdir(parents=True, exist_ok=True)
    setup_logging(args.output)

    logger.info("Pipeline start: %s", args.url)

    # ── 1. Download ──
    try:
        video_meta = download_video(args.url, args.output / "_downloads")
        logger.info("Downloaded video %s (%.1fs)", video_meta.video_id, video_meta.duration)
    except Exception as exc:
        logger.exception("Download failed: %s", exc)
        return 1

    video_output_dir = args.output / video_meta.video_id
    video_output_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. Transcript ──
    transcript = None
    try:
        transcript = get_transcript(video_meta, language=args.language)
    except Exception as exc:
        logger.exception("Transcript stage raised unexpectedly: %s", exc)

    if not transcript:
        logger.warning("No transcript available — falling back to title/description for clip selection")
        transcript_text = ""
        transcript_segments = []
        transcript_source = "none"
    else:
        transcript_text = _format_transcript_for_prompt(transcript)
        transcript_segments = transcript.segments
        transcript_source = transcript.source

    # ── 3. Clip selection via Claude ──
    try:
        clips = select_clips(
            transcript_text=transcript_text,
            video_title=video_meta.title,
            video_description=video_meta.description,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            max_clips=args.max_clips,
        )
    except Exception as exc:
        logger.exception("Clip selection failed: %s", exc)
        return 1

    if not clips:
        logger.error("Claude returned zero clips")
        return 1

    for clip in clips:
        adjust_to_sentence_boundary(clip, transcript_segments, args.min_duration, args.max_duration)

    logger.info("Selected %d clips", len(clips))

    # ── 4. Per-clip processing ──
    for clip in clips:
        try:
            _process_single_clip(
                clip=clip,
                video_meta=video_meta,
                output_dir=video_output_dir,
                transcript_segments=transcript_segments,
                use_word_timestamps=bool(transcript and transcript.has_word_timestamps),
                burn_subtitles=not args.no_subtitles,
            )
        except Exception as exc:
            logger.exception("Clip %d failed: %s", clip.clip_number, exc)

    # ── 5. Run summary ──
    report_path = video_output_dir / "shorts_report.json"
    write_shorts_report(
        output_path=report_path,
        source_video_id=video_meta.video_id,
        source_video_title=video_meta.title,
        source_url=args.url,
        clips=clips,
        transcript_source=transcript_source,
    )
    logger.info("Wrote summary: %s", report_path)

    return 0


def _process_single_clip(
    clip: SelectedClip,
    video_meta,
    output_dir: Path,
    transcript_segments,
    use_word_timestamps: bool,
    burn_subtitles: bool,
) -> None:
    clip_tag = f"clip_{clip.clip_number:02d}"

    raw_path = output_dir / f"{clip_tag}_raw.mp4"
    cut_clip(video_meta.video_path, clip.start_seconds, clip.end_seconds, raw_path)

    subtitle_path: Path | None = None
    if burn_subtitles and transcript_segments:
        if use_word_timestamps:
            subtitle_path = write_karaoke_ass(
                transcript_segments,
                output_dir / f"{clip_tag}_subtitles.ass",
                clip.start_seconds, clip.end_seconds,
            )
        else:
            subtitle_path = write_srt(
                transcript_segments,
                output_dir / f"{clip_tag}_subtitles.srt",
                clip.start_seconds, clip.end_seconds,
            )

    final_path = output_dir / f"{clip_tag}_final.mp4"
    process_to_vertical(raw_path, final_path, subtitle_path=subtitle_path)

    if final_path.stat().st_size < MIN_VALID_CLIP_BYTES:
        logger.warning("Final clip %s is suspiciously small (%d bytes)",
                       final_path, final_path.stat().st_size)

    try:
        publish_meta = generate_publish_metadata(clip, video_meta.title)
        clip.title = publish_meta["title"]
        clip.description = publish_meta["description"]
        clip.pinned_comment = publish_meta["pinned_comment"]
    except Exception as exc:
        logger.warning("Publish metadata generation failed for clip %d: %s",
                       clip.clip_number, exc)

    write_clip_metadata(
        output_dir / f"{clip_tag}_metadata.json",
        clip=clip,
        source_video_id=video_meta.video_id,
        source_video_title=video_meta.title,
    )
    logger.info("Clip %d complete: %s", clip.clip_number, final_path)


def _format_transcript_for_prompt(transcript) -> str:
    """Format transcript as [HH:MM:SS - HH:MM:SS] text lines for the model prompt."""
    lines = []
    for seg in transcript.segments:
        lines.append(f"[{_seconds_to_hms(seg.start)} - {_seconds_to_hms(seg.end)}] {seg.text}")
    return "\n".join(lines)


def _seconds_to_hms(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


if __name__ == "__main__":
    sys.exit(main())
