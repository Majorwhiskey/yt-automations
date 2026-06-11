"""Build a publish schedule for generated Shorts and optionally upload them to YouTube.

Schedule: clips are ranked by their `score` (highest first) and assigned to daily
slots at 08:00, 13:00, and 20:00 Asia/Kolkata, starting the day after this script
is run.

Usage:
    # Write schedule.json next to the clips (no upload)
    python upload_scheduler.py output/GpQSUjNsNm0

    # Also upload each clip to YouTube as a scheduled (private -> public) video.
    # Requires client_secret.json (OAuth client) in the project root.
    python upload_scheduler.py output/GpQSUjNsNm0 --upload
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("Asia/Kolkata")
DAILY_SLOTS = (time(8, 0), time(13, 0), time(20, 0))
CATEGORY_ID = "24"  # Entertainment
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def build_schedule(report: dict, start_date: datetime | None = None) -> list[dict]:
    """Assign each clip a publish datetime, highest score first."""
    clips = sorted(report["clips"], key=lambda c: c["score"], reverse=True)

    if start_date is None:
        start_date = datetime.now(TIMEZONE) + timedelta(days=1)
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    schedule = []
    for index, clip in enumerate(clips):
        day_offset, slot_index = divmod(index, len(DAILY_SLOTS))
        slot_time = DAILY_SLOTS[slot_index]
        publish_dt = (start_date + timedelta(days=day_offset)).replace(
            hour=slot_time.hour, minute=slot_time.minute
        )
        schedule.append(
            {
                "clip_number": clip["clip_number"],
                "file": f"clip_{clip['clip_number']:02d}_final.mp4",
                "title": clip["suggested_title"],
                "description": clip["suggested_description"],
                "score": clip["score"],
                "publish_at": publish_dt.isoformat(),
            }
        )
    return schedule


def extract_tags(description: str) -> list[str]:
    return [word.lstrip("#") for word in description.split() if word.startswith("#")]


def get_authenticated_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    token_path = Path(TOKEN_FILE)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(CLIENT_SECRET_FILE).exists():
                raise SystemExit(
                    f"Missing {CLIENT_SECRET_FILE}. Download an OAuth client (Desktop app) "
                    "from the Google Cloud Console and save it here."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds)


def upload_clip(youtube, clip_dir: Path, entry: dict) -> str:
    from googleapiclient.http import MediaFileUpload

    publish_at_utc = datetime.fromisoformat(entry["publish_at"]).astimezone(ZoneInfo("UTC"))
    body = {
        "snippet": {
            "title": entry["title"][:100],
            "description": entry["description"],
            "tags": extract_tags(entry["description"]),
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(clip_dir / entry["file"]), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Clip %s upload progress: %d%%", entry["clip_number"], int(status.progress() * 100))

    video_id = response["id"]
    logger.info(
        "Uploaded clip %s as video %s, scheduled for %s",
        entry["clip_number"],
        video_id,
        entry["publish_at"],
    )
    return video_id


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip_dir", type=Path, help="Directory containing shorts_report.json and clip_*.mp4")
    parser.add_argument("--upload", action="store_true", help="Upload clips to YouTube as scheduled videos")
    args = parser.parse_args()

    report_path = args.clip_dir / "shorts_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    schedule = build_schedule(report)
    schedule_path = args.clip_dir / "schedule.json"
    schedule_path.write_text(json.dumps(schedule, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote schedule for %d clips to %s", len(schedule), schedule_path)

    for entry in schedule:
        logger.info("  clip_%02d -> %s | %s", entry["clip_number"], entry["publish_at"], entry["title"])

    if args.upload:
        youtube = get_authenticated_service()
        for entry in schedule:
            entry["video_id"] = upload_clip(youtube, args.clip_dir, entry)
        schedule_path.write_text(json.dumps(schedule, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Updated %s with uploaded video IDs", schedule_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
