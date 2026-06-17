# YouTube Shorts Automation Tool

End-to-end pipeline that takes a YouTube URL and produces polished, vertical 9:16
Shorts with AI-selected segments, burned-in subtitles, and ready-to-publish titles
and descriptions — then schedules and uploads them straight to your channel.

## TL;DR — once set up, this is the whole workflow

```sh
shorts "https://youtu.be/VIDEO_ID"
```

That one command downloads the video, builds the clips, and uploads them to your
YouTube channel as scheduled posts. The rest of this README is the one-time setup
to get there.

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
10. **Schedule** clips by score into daily slots and **upload** them to YouTube as
    scheduled (private → public) videos

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

> **Windows:** `requirements.txt` includes `tzdata`, which Python's `zoneinfo` needs on
> Windows to resolve the `Asia/Kolkata` schedule timezone. Don't skip it.

### 3. YouTube upload access (one-time, only needed for `--upload`)

The upload step uses the YouTube Data API v3 with your own Google account.

1. In the [Google Cloud Console](https://console.cloud.google.com), create (or pick) a
   project, then **APIs & Services → Library → YouTube Data API v3 → Enable**.
2. **APIs & Services → OAuth consent screen** (a.k.a. **Audience**):
   - User type **External**, publishing status **Testing**.
   - Under **Test users**, add the Google account that owns your channel.
     *(In Testing mode only listed test users can authorize — skipping this gives
     `access_denied`. Using `Internal` on a personal Gmail gives `org_internal`.)*
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID →
   Application type: Desktop app**. Download the JSON, rename it to
   **`client_secret.json`**, and place it in this folder (next to `main.py`).
4. The first `--upload` run opens a browser to log in and grant access; it then caches
   a `token.json` so every later run is silent.

> `client_secret.json` and `token.json` are secrets — they are gitignored and must
> never be committed or shared. If exposed, rotate them in the Cloud Console.

## Usage

### Easiest: one command for everything

```sh
# From this folder:
.venv\Scripts\python.exe make_shorts.py "https://youtu.be/VIDEO_ID"

# Or, via the Windows launcher (works from any directory once on PATH):
shorts "https://youtu.be/VIDEO_ID"
```

`make_shorts.py` runs generation, finds the output folder for that URL, and uploads
the clips on a schedule. Only if generation succeeds does it proceed to upload.

| Flag | Description |
|---|---|
| `--no-upload` | Generate and review clips only; skip the YouTube upload. |
| *(any other flag)* | Forwarded to `main.py` (e.g. `--max-clips 7`, `--max-duration 75`). |

**Put `shorts` on your PATH** (run once in PowerShell, then open a new terminal):

```powershell
$dir = "<full path to youtube_shorts_tool>"
[Environment]::SetEnvironmentVariable("Path", "$([Environment]::GetEnvironmentVariable('Path','User'));$dir", "User")
```

### Run the steps individually

```sh
# 1. Generate clips only
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"

# 2. Schedule + upload an already-generated folder
python upload_scheduler.py output/VIDEO_ID --upload

# Build the schedule.json without uploading (preview the plan)
python upload_scheduler.py output/VIDEO_ID
```

Clips are ranked by Claude's `score` (best first) and assigned to daily slots at
**08:00, 13:00, 20:00 Asia/Kolkata**, starting the day after you run it. Each upload
is created **private** with a `publishAt` time, so it auto-publishes at its slot.

> **Quota:** each upload costs ~1,600 of the default 10,000 daily API units — roughly
> **6 uploads/day**. One source video's worth of clips is well within that.

### `main.py` optional flags

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
    ├── schedule.json               # publish slots + uploaded video IDs
    └── debug.log                   # per-run debug log
```

## Configuration

All tunables live in `config.py`:

- **`TARGET_LUFS`** — YouTube's recommended -14 LUFS
- **`VERTICAL_WIDTH` / `VERTICAL_HEIGHT`** — output resolution (1080×1920)
- **`SUBTITLE_FONT_SIZE`** — 64pt by default
- **`WHISPER_MODEL`** — `base` is the sweet spot; use `small` or `medium` for higher accuracy
- **`FADE_IN_DURATION` / `FADE_OUT_DURATION`** — 0.3s each

The publish schedule lives in `upload_scheduler.py`: edit `DAILY_SLOTS` (the posting
times) and `TIMEZONE` to fit your audience.

## Error handling

- If a video has no captions and Whisper fails, the tool logs the failure and proceeds
  using the video's title and description for clip selection.
- If a Claude-selected clip falls outside the min/max duration window, the boundary is
  snapped to the nearest transcript segment.
- Each final clip is checked against a minimum file size; suspiciously small clips are
  flagged in the log.
- All exceptions per clip are caught — one failed clip does not halt the rest of the run.
- All exceptions land in `debug.log` with timestamps.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Error 403: org_internal` during login | OAuth consent screen is set to **Internal**. Switch it to **External** (Audience page). |
| `Error 403: access_denied` | Your account isn't a **Test user**. Add it under the consent screen's Test users. |
| `ZoneInfoNotFoundError: 'Asia/Kolkata'` | Install `tzdata` (`pip install tzdata`) — needed on Windows. |
| `Missing client_secret.json` | Download a Desktop-app OAuth client and save it here as `client_secret.json` (see Setup 3). |
| `getaddrinfo failed` / download stalls | Network/DNS hiccup. The downloader auto-retries; just re-run the command. |
| Login worked once, now errors | Delete `token.json` and re-run `--upload` to re-auth cleanly. |
| `quotaExceeded` on upload | YouTube's ~6 uploads/day default cap was hit. Wait for the daily reset or request more quota. |

## Cost notes

Claude calls go through the local `claude` CLI, so they bill against your Claude Pro
subscription (no per-token API charges). Per video, the pipeline makes:

- 1 clip-selection call (~15k input tokens)
- 1 metadata call per clip (~300 tokens each)

Heavy use can hit Pro's per-5-hour usage cap. If you'd rather use the paid API for
unlimited throughput, swap `_invoke_claude` in `clip_selector.py` for the
`anthropic.Anthropic` SDK.
