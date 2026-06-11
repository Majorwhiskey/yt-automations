"""FFmpeg-based video processing: cutting, vertical reframing, subtitle burn-in, audio normalization."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import cv2

from config import (
    FADE_IN_DURATION,
    FADE_OUT_DURATION,
    LUFS_LRA,
    LUFS_TRUE_PEAK,
    TARGET_FPS,
    TARGET_LUFS,
    VERTICAL_HEIGHT,
    VERTICAL_WIDTH,
)

logger = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def cut_clip(
    source_video: Path,
    start_seconds: float,
    end_seconds: float,
    output_path: Path,
) -> Path:
    """Cut a frame-accurate segment, re-encoding via libx264.

    Uses a two-stage seek: a fast keyframe-aligned `-ss` before `-i` jumps close to
    the target, then a small `-ss` after `-i` decodes only the remaining ~30s for
    frame-accurate positioning. This avoids decoding the entire video from the start
    for clips that occur late in long source videos.
    """
    duration = end_seconds - start_seconds
    seek_buffer = 30.0
    coarse_seek = max(0.0, start_seconds - seek_buffer)
    fine_seek = start_seconds - coarse_seek
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{coarse_seek:.3f}",
        "-i", str(source_video),
        "-ss", f"{fine_seek:.3f}",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-preset", "fast",
        "-crf", "18",
        str(output_path),
    ]
    _run(cmd, "cut_clip")
    return output_path


def detect_crop_center(video_path: Path, sample_interval: float = 0.5) -> tuple[int, int] | None:
    """Find the average horizontal center of the most prominent face across the clip.

    Falls back to None if no faces are detected — caller should center-crop in that case.
    Returns (cx, cy) in source-video pixel coordinates.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frame_step = max(1, int(fps * sample_interval))
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    centers: list[tuple[int, int]] = []
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % frame_step == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.2, 5, minSize=(80, 80))
            if len(faces):
                # Largest face wins — assume it's the subject
                x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
                centers.append((x + w // 2, y + h // 2))
        frame_idx += 1

    cap.release()
    if not centers:
        return None
    avg_cx = sum(c[0] for c in centers) // len(centers)
    avg_cy = sum(c[1] for c in centers) // len(centers)
    return avg_cx, avg_cy


def process_to_vertical(
    input_clip: Path,
    output_path: Path,
    subtitle_path: Path | None,
    fade_in: float = FADE_IN_DURATION,
    fade_out: float = FADE_OUT_DURATION,
) -> Path:
    """Convert to 9:16 with smart crop, burn subtitles, normalize audio, add fades."""
    duration = _probe_duration(input_clip)
    src_w, src_h = _probe_dimensions(input_clip)

    crop_filter = _build_crop_filter(input_clip, src_w, src_h)
    video_filters = [crop_filter]

    if fade_in > 0:
        video_filters.append(f"fade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        fade_out_start = max(0.0, duration - fade_out)
        video_filters.append(f"fade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}")

    if subtitle_path:
        sub_filter = _build_subtitle_filter(subtitle_path)
        video_filters.append(sub_filter)

    audio_filter = (
        f"loudnorm=I={TARGET_LUFS}:TP={LUFS_TRUE_PEAK}:LRA={LUFS_LRA},"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_clip),
        "-vf", ",".join(video_filters),
        "-af", audio_filter,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(TARGET_FPS),
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd, "process_to_vertical")
    return output_path


def _build_crop_filter(input_clip: Path, src_w: int, src_h: int) -> str:
    """Build a crop+scale filter chain that yields a 1080x1920 vertical frame.

    Logic:
    - Compute the 9:16 crop region that fits within the source.
    - Center it on the detected face (if any), otherwise center horizontally.
    - Clamp the crop window to stay within source bounds.
    - Scale the crop to the target resolution.
    """
    target_aspect = VERTICAL_WIDTH / VERTICAL_HEIGHT  # 0.5625

    if src_w / src_h > target_aspect:
        # Wider than 9:16 — crop width
        crop_h = src_h
        crop_w = int(crop_h * target_aspect)
        focus = detect_crop_center(input_clip)
        if focus:
            cx = focus[0]
        else:
            cx = src_w // 2
        crop_x = max(0, min(src_w - crop_w, cx - crop_w // 2))
        crop_y = 0
    else:
        # Taller than 9:16 — crop height
        crop_w = src_w
        crop_h = int(crop_w / target_aspect)
        crop_x = 0
        crop_y = max(0, (src_h - crop_h) // 2)

    return (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:flags=lanczos"
    )


def _build_subtitle_filter(subtitle_path: Path) -> str:
    """Build the subtitles filter. ASS files use the ass filter; SRT uses subtitles.

    Filenames must be escaped for ffmpeg's filter graph syntax: backslashes doubled,
    colons escaped, single quotes wrapped.
    """
    path_str = str(subtitle_path.resolve()).replace("\\", "/").replace(":", "\\:")
    if subtitle_path.suffix.lower() == ".ass":
        return f"ass='{path_str}'"
    return f"subtitles='{path_str}'"


def _probe_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {result.stderr}")
    return float(result.stdout.strip())


def _probe_dimensions(video_path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {result.stderr}")
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def _run(cmd: list[str], label: str) -> None:
    logger.debug("Running %s: %s", label, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"ffmpeg ({label}) failed: {result.stderr[-2000:]}")
