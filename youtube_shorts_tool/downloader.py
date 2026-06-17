"""Video ingestion via yt-dlp."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp

from config import VIDEO_FORMAT

logger = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    video_id: str
    title: str
    description: str
    duration: float
    upload_date: str
    uploader: str
    video_path: Path
    audio_path: Path
    subtitle_path: Path | None = None
    auto_subtitle_path: Path | None = None
    tags: list[str] = field(default_factory=list)


def download_video(url: str, output_dir: Path) -> VideoMetadata:
    """Download a YouTube video and extract its audio, returning combined metadata.

    yt-dlp stores progress in a single download dict; we use the post-download
    `info_dict` to resolve final paths because filename templates can change after
    merging streams.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    video_opts = {
        "format": VIDEO_FORMAT,
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB"],
        "subtitlesformat": "vtt",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Resilience: chunked HTTP downloads + aggressive retries to survive
        # flaky residential connections. http_chunk_size shrinks the per-request
        # window so a mid-stream stall costs ~1 MB instead of the full file.
        "retries": 30,
        "fragment_retries": 30,
        "file_access_retries": 10,
        "socket_timeout": 60,
        "http_chunk_size": 1024 * 1024,  # 1 MB chunks
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
    }

    with yt_dlp.YoutubeDL(video_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info["id"]
    video_path = output_dir / f"{video_id}.mp4"
    if not video_path.exists():
        # yt-dlp occasionally falls back to mkv when merging certain codecs
        for candidate in output_dir.glob(f"{video_id}.*"):
            if candidate.suffix in {".mp4", ".mkv", ".webm"}:
                video_path = candidate
                break

    audio_path = output_dir / f"{video_id}.m4a"
    _extract_audio(video_path, audio_path)

    subtitle_path = _find_subtitle(output_dir, video_id, auto=False)
    auto_subtitle_path = _find_subtitle(output_dir, video_id, auto=True)

    return VideoMetadata(
        video_id=video_id,
        title=info.get("title", ""),
        description=info.get("description", "") or "",
        duration=float(info.get("duration", 0) or 0),
        upload_date=info.get("upload_date", "") or "",
        uploader=info.get("uploader", "") or "",
        video_path=video_path,
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        auto_subtitle_path=auto_subtitle_path,
        tags=list(info.get("tags") or []),
    )


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    """Extract audio losslessly via ffmpeg (codec copy, no re-encode)."""
    import subprocess

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "copy",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Fallback: re-encode to AAC. Some containers refuse stream copy.
        cmd_reencode = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "aac",
            "-b:a",
            "192k",
            str(audio_path),
        ]
        result = subprocess.run(cmd_reencode, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr}")


def _find_subtitle(output_dir: Path, video_id: str, auto: bool) -> Path | None:
    """Locate the best-matching subtitle file written by yt-dlp.

    Manual subtitles take precedence over auto-generated. yt-dlp appends `.lang.vtt`
    for manual and `.lang.vtt` for auto, sometimes with no clear differentiator —
    we sniff the file content to disambiguate.
    """
    candidates = list(output_dir.glob(f"{video_id}*.vtt"))
    if not candidates:
        return None

    for cand in candidates:
        try:
            head = cand.read_text(encoding="utf-8", errors="ignore")[:500].lower()
        except OSError:
            continue
        is_auto = "kind: captions" in head or "auto-generated" in head
        if auto and is_auto:
            return cand
        if not auto and not is_auto:
            return cand
    return candidates[0] if not auto else None
