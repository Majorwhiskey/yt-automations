"""Claude-CLI integration for AI-driven clip selection and metadata generation.

Uses the locally installed `claude` CLI (Claude Code) so the pipeline runs against
the user's existing Claude Pro subscription instead of a paid API key.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

from config import HARD_MAX_CLIPS

logger = logging.getLogger(__name__)


CLAUDE_CLI_TIMEOUT = 300  # seconds — generous for long transcripts


@dataclass
class SelectedClip:
    clip_number: int
    start_seconds: float
    end_seconds: float
    hook: str
    topic: str
    score: float
    title: str = ""
    description: str = ""


_CLIP_SELECTION_PROMPT = """You are an expert short-form video editor who has produced thousands of viral \
YouTube Shorts. You are given a full video transcript with timestamps. Your job is to identify the \
most compelling segments to extract as standalone Shorts.

Selection criteria — every clip MUST satisfy ALL of these:
1. Duration between {min_duration} and {max_duration} seconds.
2. Self-contained — viewer should understand the clip without prior context.
3. Starts with a strong hook: a surprising claim, a provocative question, or a bold statement.
4. Has a clear beginning, middle, and end (a complete thought or mini-story).
5. Starts and ends on sentence boundaries — never mid-sentence.
6. Avoids filler, throat-clearing, and run-up exposition at the start.

Scoring rubric (0–10):
- 9–10: viral potential — strong hook + payoff + quotable line
- 7–8: strong standalone — clear value, polished structure
- 5–6: decent but missing a sharp hook or payoff
- Below 5: do not return

Return between 3 and {max_clips} clips, ordered by score (highest first). All timestamps must be valid \
HH:MM:SS format and fall within the transcript range.

OUTPUT FORMAT — respond with ONLY a JSON object matching this schema, wrapped in a ```json fence. \
No prose before or after.

```json
{{
  "clips": [
    {{
      "clip_number": 1,
      "start_time": "HH:MM:SS",
      "end_time": "HH:MM:SS",
      "hook": "first sentence of the clip",
      "topic": "one-sentence subject summary",
      "score": 8.5
    }}
  ]
}}
```

Video title: {video_title}

Video description (excerpt): {description_excerpt}

Transcript (each line: [HH:MM:SS - HH:MM:SS] text):
{transcript_text}"""


_PUBLISH_METADATA_PROMPT = """Source video: {video_title}

Clip hook: {hook}
Clip topic: {topic}

Write a YouTube Shorts title (max 60 characters, no emojis, no clickbait, no ALL CAPS) and a \
two-sentence description ending with exactly three relevant hashtags.

OUTPUT FORMAT — respond with ONLY a JSON object matching this schema, wrapped in a ```json fence. \
No prose before or after.

```json
{{
  "title": "string",
  "description": "string"
}}
```
"""


def select_clips(
    transcript_text: str,
    video_title: str,
    video_description: str,
    min_duration: int,
    max_duration: int,
    max_clips: int,
) -> list[SelectedClip]:
    """Ask Claude (via the local CLI) to pick the best segments."""
    description_excerpt = (video_description or "").strip()[:500]
    prompt = _CLIP_SELECTION_PROMPT.format(
        min_duration=min_duration,
        max_duration=max_duration,
        max_clips=max_clips,
        video_title=video_title,
        description_excerpt=description_excerpt,
        transcript_text=transcript_text,
    )

    raw = _invoke_claude(prompt)
    payload = _extract_json(raw)
    if not payload or "clips" not in payload:
        raise RuntimeError(f"Claude returned no parseable clip selection. Raw output: {raw[:500]}")

    selected: list[SelectedClip] = []
    for cand in payload["clips"]:
        try:
            start_s = _hms_to_seconds(cand["start_time"])
            end_s = _hms_to_seconds(cand["end_time"])
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping clip with bad timestamp: %s", exc)
            continue

        duration = end_s - start_s
        if duration < min_duration or duration > max_duration:
            logger.warning(
                "Clip %s duration %.1fs outside [%d,%d] — keeping for boundary adjustment",
                cand.get("clip_number"), duration, min_duration, max_duration,
            )

        selected.append(
            SelectedClip(
                clip_number=int(cand["clip_number"]),
                start_seconds=start_s,
                end_seconds=end_s,
                hook=str(cand.get("hook", "")),
                topic=str(cand.get("topic", "")),
                score=float(cand.get("score", 0.0)),
            )
        )

    selected.sort(key=lambda c: c.score, reverse=True)
    return selected[: min(max_clips, HARD_MAX_CLIPS)]


def generate_publish_metadata(clip: SelectedClip, video_title: str) -> dict[str, str]:
    """Generate a Shorts-ready title and description for a single clip."""
    prompt = _PUBLISH_METADATA_PROMPT.format(
        video_title=video_title,
        hook=clip.hook,
        topic=clip.topic,
    )

    raw = _invoke_claude(prompt)
    payload = _extract_json(raw)
    if not payload:
        return {"title": clip.topic[:60], "description": clip.hook}
    return {
        "title": str(payload.get("title", clip.topic))[:60],
        "description": str(payload.get("description", clip.hook)),
    }


def _invoke_claude(prompt: str) -> str:
    """Run `claude -p` as a subprocess, feeding the prompt via stdin.

    stdin is used (not argv) because transcripts can exceed Windows' 8191-char
    command-line limit. The CLI uses the user's logged-in Claude Pro session.
    """
    if not shutil.which("claude"):
        raise RuntimeError(
            "`claude` CLI not found on PATH. Install Claude Code (https://claude.com/claude-code) "
            "and ensure you are logged in with `claude login`."
        )

    logger.info("Invoking Claude CLI (prompt %d chars)", len(prompt))
    try:
        result = subprocess.run(
            ["claude", "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CLAUDE_CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Claude CLI timed out after {CLAUDE_CLI_TIMEOUT}s") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"Claude CLI failed (exit {result.returncode}): {result.stderr.strip()[:500]}"
        )

    return result.stdout


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
    """Pull the first JSON object from a Claude response.

    Tolerates: bare JSON, ```json fenced blocks, leading/trailing prose.
    """
    if not raw:
        return None

    fence_match = _JSON_FENCE.search(raw)
    if fence_match:
        candidate = fence_match.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Fallback: bare JSON — find the first { and try parsing until the matching }
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _hms_to_seconds(hms: str) -> float:
    parts = hms.strip().split(":")
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    raise ValueError(f"Invalid timestamp: {hms}")


def adjust_to_sentence_boundary(
    clip: SelectedClip,
    transcript_segments,
    min_duration: int,
    max_duration: int,
) -> SelectedClip:
    """Snap clip endpoints to the nearest segment boundary in the transcript.

    Claude's timestamps are sometimes off by a sentence — extending to the next
    segment boundary keeps the clip from cutting mid-thought.
    """
    if not transcript_segments:
        return clip

    snapped_start = clip.start_seconds
    snapped_end = clip.end_seconds

    for seg in transcript_segments:
        if abs(seg.start - clip.start_seconds) < 1.5:
            snapped_start = seg.start
            break
    for seg in transcript_segments:
        if abs(seg.end - clip.end_seconds) < 1.5:
            snapped_end = seg.end
            break

    duration = snapped_end - snapped_start
    if duration < min_duration:
        for seg in transcript_segments:
            if seg.start >= snapped_end:
                snapped_end = seg.end
                if snapped_end - snapped_start >= min_duration:
                    break
    if snapped_end - snapped_start > max_duration:
        snapped_end = snapped_start + max_duration

    clip.start_seconds = snapped_start
    clip.end_seconds = snapped_end
    return clip
