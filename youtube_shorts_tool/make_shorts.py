"""One-command wrapper: paste a YouTube URL, get scheduled Shorts on your channel.

Runs the full pipeline end to end:
    1. main.py            — download, transcribe, select, cut, reframe, subtitle
    2. upload_scheduler.py — rank by score, schedule, and upload to YouTube

Usage:
    # The only thing you ever need to type:
    python make_shorts.py "https://youtu.be/C_OZ-jpW8hA"

    # Generate clips but don't upload (review them first):
    python make_shorts.py "https://youtu.be/C_OZ-jpW8hA" --no-upload

    # Pass-through tuning flags are forwarded to main.py:
    python make_shorts.py "<url>" --max-clips 7 --max-duration 75

Prerequisites are the same as the individual steps: the `claude` CLI logged in,
ffmpeg on PATH, and (for upload) client_secret.json + a cached token.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from config import OUTPUT_DIR

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent


def run_generation(url: str, passthrough: list[str]) -> int:
    """Invoke main.py for clip generation, streaming its output live."""
    cmd = [sys.executable, str(HERE / "main.py"), "--url", url, *passthrough]
    logger.info("Generating clips: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=HERE).returncode


def find_clip_dir(url: str, output_root: Path) -> Path | None:
    """Locate the output folder this run produced by matching the source URL.

    main.py writes output/<video_id>/shorts_report.json with the source_url
    embedded, so we match on that rather than re-parsing the URL ourselves.
    Falls back to the most recently written report if no exact URL match.
    """
    reports = sorted(
        output_root.glob("*/shorts_report.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not reports:
        return None

    for report in reports:
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("source_url") == url:
            return report.parent

    # No URL match — assume the newest report is this run's.
    logger.warning("No report matched the URL exactly; using newest: %s", reports[0].parent)
    return reports[0].parent


def run_upload(clip_dir: Path) -> int:
    """Invoke upload_scheduler.py with --upload, streaming its output live."""
    cmd = [sys.executable, str(HERE / "upload_scheduler.py"), str(clip_dir), "--upload"]
    logger.info("Scheduling + uploading: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=HERE).returncode


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="Generate Shorts from a YouTube URL and upload them on a schedule.",
    )
    parser.add_argument("url", help="Source YouTube URL")
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Only generate clips; skip the YouTube upload step.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR,
        help="Output root directory (must match what main.py uses).",
    )
    # Everything after `--` (or any unrecognized flags) is forwarded to main.py.
    args, passthrough = parser.parse_known_args()

    output_root = args.output

    if run_generation(args.url, passthrough) != 0:
        logger.error("Clip generation failed — aborting before upload.")
        return 1

    clip_dir = find_clip_dir(args.url, output_root)
    if clip_dir is None:
        logger.error("Could not locate generated clips under %s", output_root)
        return 1
    logger.info("Clips ready in %s", clip_dir)

    if args.no_upload:
        logger.info("--no-upload set: skipping YouTube upload. Review clips in %s", clip_dir)
        return 0

    if run_upload(clip_dir) != 0:
        logger.error("Upload step failed. Clips are intact in %s — rerun:", clip_dir)
        logger.error("    %s upload_scheduler.py %s --upload", Path(sys.executable).name, clip_dir)
        return 1

    logger.info("Done. All clips generated and scheduled from %s", clip_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
