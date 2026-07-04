"""The booking object and its validation. Pure Python, no LLM anywhere:
whether a booking is possible is decided here, never by the model."""

import re
from datetime import date, datetime, time, timedelta
from typing import Callable, Literal

from pydantic import BaseModel

from app.shop import BARBERS, CLOSE_TIME, CLOSED_WEEKDAY, OPEN_TIME, SERVICES, TZ

BusyLookup = Callable[[str, datetime, datetime], list[tuple[datetime, datetime]]]

SLOT_STEP_MINUTES = 15
# Saudi mobile, spaces ignored: 05 plus 8 digits, or +9665 plus 8 digits.
# Strict on purpose: a cut-off transcript like "0501234" must fail here,
# not slip through and get caught only at the read-back.
PHONE_RE = re.compile(r"^(?:\+9665|05)[0-9]{8}$")

FIELD_ORDER = ["service", "barber", "day", "start", "name", "phone"]


class Booking(BaseModel):
    service: str | None = None
    barber: str | None = None
    day: date | None = None
    start: time | None = None
    name: str | None = None
    phone: str | None = None

    @property
    def minutes(self) -> int | None:
        for s in SERVICES:
            if s.name == self.service:
                return s.minutes
        return None

    def missing(self) -> list[str]:
        return [f for f in FIELD_ORDER if getattr(self, f) is None]

    def start_dt(self) -> datetime | None:
        if self.day is None or self.start is None:
            return None
        return datetime.combine(self.day, self.start, tzinfo=TZ)

    def summary(self) -> str:
        parts = []
        if self.service:
            parts.append(self.service)
        if self.barber:
            parts.append(f"with {self.barber}")
        if self.day:
            parts.append(f"on {self.day.strftime('%A %B %-d')}")
        if self.start:
            parts.append(f"at {self.start.strftime('%H:%M')}")
        if self.name:
            parts.append(f"for {self.name}")
        return " ".join(parts) if parts else "empty booking"


class Problem(BaseModel):
    code: Literal[
        "UNKNOWN_SERVICE",
        "UNKNOWN_BARBER",
        "PAST",
        "CLOSED_DAY",
        "OUTSIDE_HOURS",
        "SLOT_TAKEN",
        "BAD_PHONE",
    ]
    detail: str
    alternatives: list[str] = []


def day_window(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, OPEN_TIME, tzinfo=TZ),
        datetime.combine(day, CLOSE_TIME, tzinfo=TZ),
    )


def free_slots(
    barber: str, day: date, minutes: int, busy_lookup: BusyLookup, now: datetime
) -> list[datetime]:
    """All start times on `day` where `minutes` fit inside opening hours and free time."""
    if day.weekday() == CLOSED_WEEKDAY:
        return []
    day_open, day_close = day_window(day)
    busy = busy_lookup(barber, day_open, day_close)
    slots = []
    cursor = day_open
    while cursor + timedelta(minutes=minutes) <= day_close:
        if cursor >= now and all(
            cursor + timedelta(minutes=minutes) <= b_start or cursor >= b_end
            for b_start, b_end in busy
        ):
            slots.append(cursor)
        cursor += timedelta(minutes=SLOT_STEP_MINUTES)
    return slots


def _alternatives(booking: Booking, busy_lookup: BusyLookup, now: datetime) -> list[str]:
    """Honest options computed from real availability: nearest same-day slots for the
    requested barber, plus the other barber at the requested time if free."""
    assert booking.barber and booking.day and booking.start and booking.minutes
    wanted = booking.start_dt()
    assert wanted is not None
    options = []

    same_day = free_slots(booking.barber, booking.day, booking.minutes, busy_lookup, now)
    for slot in sorted(same_day, key=lambda s: abs(s - wanted))[:2]:
        options.append(f"{booking.barber} at {slot.strftime('%H:%M')}")

    for other in BARBERS:
        if other != booking.barber:
            other_slots = free_slots(other, booking.day, booking.minutes, busy_lookup, now)
            if wanted in other_slots:
                options.append(f"{other} at {booking.start.strftime('%H:%M')}")
    return options


def validate(booking: Booking, busy_lookup: BusyLookup, now: datetime) -> Problem | None:
    """Check every filled field against the closed world and real availability.
    Called after ANY field changes, so a changed date re-checks the time, a changed
    service re-checks the duration fit, and so on. Returns the first problem found."""
    if booking.service is not None and booking.minutes is None:
        names = ", ".join(s.name for s in SERVICES)
        return Problem(code="UNKNOWN_SERVICE", detail=f"We only offer: {names}.")

    if booking.barber is not None and booking.barber not in BARBERS:
        return Problem(code="UNKNOWN_BARBER", detail=f"Our barbers are {' and '.join(BARBERS)}.")

    if booking.phone is not None and not PHONE_RE.match(booking.phone.replace(" ", "")):
        return Problem(
            code="BAD_PHONE",
            detail="That does not look like a full Saudi mobile number (05 and 8 more digits).",
        )

    if booking.day is not None:
        if booking.day < now.date():
            return Problem(code="PAST", detail="That date is in the past.")
        if booking.day.weekday() == CLOSED_WEEKDAY:
            return Problem(code="CLOSED_DAY", detail="We are closed on Fridays. Saturday works.")

    start = booking.start_dt()
    if start is not None:
        minutes = (
            booking.minutes or 30
        )  # duration unknown until service picked; assume shortest common
        day_open, day_close = day_window(booking.day)  # type: ignore[arg-type]
        if start < day_open or start + timedelta(minutes=minutes) > day_close:
            return Problem(
                code="OUTSIDE_HOURS",
                detail=f"We are open 10:00 to 22:00; a {minutes} minute appointment "
                f"at {booking.start.strftime('%H:%M')} does not fit.",  # type: ignore[union-attr]
            )
        if start < now:
            return Problem(code="PAST", detail="That time has already passed today.")
        if booking.barber is not None and booking.service is not None:
            free = free_slots(booking.barber, booking.day, minutes, busy_lookup, now)  # type: ignore[arg-type]
            if start not in free:
                return Problem(
                    code="SLOT_TAKEN",
                    detail=f"{booking.barber} is not free for {minutes} minutes "
                    f"at {booking.start.strftime('%H:%M')}.",  # type: ignore[union-attr]
                    alternatives=_alternatives(booking, busy_lookup, now),
                )
    return None
