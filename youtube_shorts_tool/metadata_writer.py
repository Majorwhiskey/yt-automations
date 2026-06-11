"""JSON metadata writers for per-clip and run-summary outputs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_clip_metadata(
    output_path: Path,
    clip,
    source_video_id: str,
    source_video_title: str,
) -> Path:
    payload = {
        "clip_number": clip.clip_number,
        "source_video_id": source_video_id,
        "source_video_title": source_video_title,
        "start_seconds": round(clip.start_seconds, 3),
        "end_seconds": round(clip.end_seconds, 3),
        "duration_seconds": round(clip.end_seconds - clip.start_seconds, 3),
        "hook": clip.hook,
        "hook_type": clip.hook_type,
        "quotable_line": clip.quotable_line,
        "topic": clip.topic,
        "score": clip.score,
        "suggested_title": clip.title,
        "suggested_description": clip.description,
        "pinned_comment": clip.pinned_comment,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def write_shorts_report(
    output_path: Path,
    source_video_id: str,
    source_video_title: str,
    source_url: str,
    clips: list,
    transcript_source: str,
) -> Path:
    payload: dict[str, Any] = {
        "source_video_id": source_video_id,
        "source_video_title": source_video_title,
        "source_url": source_url,
        "transcript_source": transcript_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clip_count": len(clips),
        "clips": [
            {
                "clip_number": c.clip_number,
                "start_seconds": round(c.start_seconds, 3),
                "end_seconds": round(c.end_seconds, 3),
                "duration_seconds": round(c.end_seconds - c.start_seconds, 3),
                "hook": c.hook,
                "hook_type": c.hook_type,
                "quotable_line": c.quotable_line,
                "topic": c.topic,
                "score": c.score,
                "suggested_title": c.title,
                "suggested_description": c.description,
                "pinned_comment": c.pinned_comment,
            }
            for c in clips
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path
