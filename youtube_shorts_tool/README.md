# YouTube Shorts Automation Tool

End-to-end pipeline that takes a YouTube URL and produces polished, vertical 9:16
Shorts with AI-selected segments, burned-in subtitles, and ready-to-publish titles
and descriptions.

## Pipeline

1. **Download** the source video with `yt-dlp`, extracting both video and audio streams
2. **Transcribe** via the video's captions, auto-captions, or Whisper as a fallback
3. **Identify** the strongest 3–7 segments by sending the transcript to Claude
4. **Cut** each segment with frame-accurate ffmpeg seeks
5. **Reframe** to 1080×1920 with face-aware smart cropping (OpenCV)
6. **Subtitle** with burned-in SRT or karaoke-style ASS (word-level highlighting if Whisper was used)
7. **Normalize** audio to -14 LUFS and add 0.3s fade in/out
8. **Title** and describe each clip via a second Claude call
9. **Report** everything to `shorts_report.json`

## Setup

### 1. System dependencies

You need `ffmpeg`, `ffprobe`, and the `claude` CLI on your PATH.

- **ffmpeg**
  - Windows: `winget install Gyan.FFmpeg` or download from [ffmpeg.org](https://ffmpeg.org/download.html)
  - macOS: `brew install ffmpeg`
  - Linux: `apt install ffmpeg`
- **Claude CLI** — install [Claude Code](https://claude.com/claude-code) and run `claude login` once.
  The pipeline shells out to `claude -p`, so it uses your existing Claude Pro subscription — no API key required.

### 2. Python dependencies

```sh
cd youtube_shorts_tool
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

> Whisper pulls in PyTorch (~2 GB). If you want to skip Whisper entirely and rely only on
> YouTube's existing captions, you can omit `openai-whisper` from the install — the tool
> will fall back gracefully when no captions exist.

## Usage

```sh
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Optional flags

| Flag | Default | Description |
|---|---|---|
| `--max-clips N` | 5 | Maximum clips to generate (capped at 7) |
| `--min-duration N` | 30 | Minimum clip length in seconds |
| `--max-duration N` | 90 | Maximum clip length in seconds |
| `--language LANG` | `en` | Whisper transcription language |
| `--no-subtitles` | — | Skip burning subtitles onto the final video |
| `--output PATH` | `output/` | Custom output directory |

### Examples

```sh
# Default: up to 5 clips, 30-90s each
python main.py --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 7 longer clips for a deeper-dive video, no subtitles
python main.py --url "https://www.youtube.com/watch?v=abc123" --max-clips 7 --max-duration 75 --no-subtitles

# Spanish-language source, custom output dir
python main.py --url "https://www.youtube.com/watch?v=xyz789" --language es --output ./mi_salida
```

## Output structure

```
output/
└── <VIDEO_ID>/
    ├── clip_01_raw.mp4             # unprocessed cut
    ├── clip_01_final.mp4           # 1080x1920 with subtitles, normalized audio
    ├── clip_01_subtitles.ass       # word-level karaoke (Whisper) or .srt (captions)
    ├── clip_01_metadata.json       # hook, topic, score, title, description
    ├── clip_02_*.mp4
    ├── ...
    ├── shorts_report.json          # summary of all clips
    └── debug.log                   # per-run debug log
```

## Configuration

All tunables live in `config.py`:

- **`TARGET_LUFS`** — YouTube's recommended -14 LUFS
- **`VERTICAL_WIDTH` / `VERTICAL_HEIGHT`** — output resolution (1080×1920)
- **`SUBTITLE_FONT_SIZE`** — 52pt by default
- **`WHISPER_MODEL`** — `base` is the sweet spot; use `small` or `medium` for higher accuracy
- **`FADE_IN_DURATION` / `FADE_OUT_DURATION`** — 0.3s each

## Error handling

- If a video has no captions and Whisper fails, the tool logs the failure and proceeds
  using the video's title and description for clip selection.
- If a Claude-selected clip falls outside the min/max duration window, the boundary is
  snapped to the nearest transcript segment.
- Each final clip is checked against a minimum file size; suspiciously small clips are
  flagged in the log.
- All exceptions per clip are caught — one failed clip does not halt the rest of the run.
- All exceptions land in `debug.log` with timestamps.

## Cost notes

Claude calls go through the local `claude` CLI, so they bill against your Claude Pro
subscription (no per-token API charges). Per video, the pipeline makes:

- 1 clip-selection call (~15k input tokens)
- 1 metadata call per clip (~300 tokens each)

Heavy use can hit Pro's per-5-hour usage cap. If you'd rather use the paid API for
unlimited throughput, swap `_invoke_claude` in `clip_selector.py` for the
`anthropic.Anthropic` SDK.
