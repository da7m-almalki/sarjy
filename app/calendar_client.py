"""Google Calendar access via the service account. The only module that talks to the calendar."""

from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import settings
from app.shop import BARBERS, CLOSE_TIME, CLOSED_WEEKDAY, OPEN_TIME, TZ

SLOT_STEP_MINUTES = 15


def _service():
    creds = service_account.Credentials.from_service_account_file(
        settings.service_account_file,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def busy_blocks(
    calendar_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    fb = (
        _service()
        .freebusy()
        .query(
            body={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": calendar_id}],
            }
        )
        .execute()
    )
    blocks = fb["calendars"][calendar_id]["busy"]
    return [
        (
            datetime.fromisoformat(b["start"]).astimezone(TZ),
            datetime.fromisoformat(b["end"]).astimezone(TZ),
        )
        for b in blocks
    ]


def next_free_slot(barber: str, minutes: int, now: datetime | None = None) -> datetime | None:
    """First gap today that fits `minutes` within opening hours, or None if none left."""
    now = now or datetime.now(TZ)
    if now.weekday() == CLOSED_WEEKDAY:
        return None
    day_open = now.replace(hour=OPEN_TIME.hour, minute=OPEN_TIME.minute, second=0, microsecond=0)
    day_close = now.replace(hour=CLOSE_TIME.hour, minute=CLOSE_TIME.minute, second=0, microsecond=0)
    busy = busy_blocks(BARBERS[barber], day_open, day_close)

    cursor = max(now, day_open)
    # round up to the next quarter hour
    remainder = (cursor.minute % SLOT_STEP_MINUTES, cursor.second, cursor.microsecond)
    if remainder != (0, 0, 0):
        cursor = cursor.replace(second=0, microsecond=0) + timedelta(
            minutes=SLOT_STEP_MINUTES - cursor.minute % SLOT_STEP_MINUTES
        )

    while cursor + timedelta(minutes=minutes) <= day_close:
        slot_end = cursor + timedelta(minutes=minutes)
        if all(slot_end <= b_start or cursor >= b_end for b_start, b_end in busy):
            return cursor
        cursor += timedelta(minutes=SLOT_STEP_MINUTES)
    return None


def availability_text() -> str:
    """One line per barber for the system prompt, based on a real free/busy call."""
    lines = []
    for barber in BARBERS:
        slot = next_free_slot(barber, minutes=30)
        if slot is None:
            lines.append(f"{barber}: no free 30 min slot left today.")
        else:
            lines.append(f"{barber}: next free slot today at {slot.strftime('%H:%M')}.")
    return " ".join(lines)
