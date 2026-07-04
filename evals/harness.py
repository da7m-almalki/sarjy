"""Runs scripted conversations against the real pipeline (real LLM calls) with a
fake calendar and a frozen clock, then checks the side effects: did the calendar
end up in exactly the right state? Replies are judged by outcomes, not wording."""

import tempfile
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from pathlib import Path

from app import flow
from app.config import settings
from app.shop import TZ

FROZEN_NOW = datetime(2026, 7, 4, 10, 0, tzinfo=TZ)  # Saturday morning


class FakeCalendar:
    """In-memory stand-in for Google Calendar: pre-seeded busy blocks per barber,
    records every create/delete."""

    def __init__(self, busy: dict[str, list[tuple[datetime, datetime]]] | None = None):
        self.busy: dict[str, list[tuple[datetime, datetime]]] = busy or {"Ali": [], "Salem": []}
        self.created: list[dict] = []
        self.deleted: list[str] = []

    def busy_lookup(self, barber: str, start: datetime, end: datetime):
        return [b for b in self.busy.get(barber, []) if b[1] > start and b[0] < end]

    def create_event(
        self, barber: str, start: datetime, minutes: int, summary: str, description: str = ""
    ) -> str:
        self.created.append(
            {"barber": barber, "start": start, "minutes": minutes, "summary": summary}
        )
        return f"fake-event-{len(self.created)}"

    def delete_event(self, barber: str, event_id: str) -> None:
        self.deleted.append(event_id)


@dataclass
class Expect:
    created: int = 0  # how many events must exist at the end
    created_start: str | None = None  # ISO start of the last created event
    created_barber: str | None = None
    deleted: int = 0
    final_state: str | None = None
    booking_discarded: bool = False  # session booking must be empty at the end


@dataclass
class Scenario:
    name: str
    turns: list[str]
    expect: Expect
    busy: dict | None = None  # pre-seeded busy blocks
    recovery: bool = False  # counts toward the recovery metric
    pre_turns: list[str] = dc_field(default_factory=list)  # setup turns (not counted)


def run_scenario(scenario: Scenario, index: int) -> tuple[bool, list[str], FakeCalendar]:
    fake = FakeCalendar(scenario.busy)
    device = f"eval-{index}-{scenario.name.replace(' ', '-')}"

    # isolate memory in a throwaway database, freeze the clock, fake the calendar
    settings.db_path = str(Path(tempfile.mkdtemp()) / "eval.db")
    flow.busy_lookup = fake.busy_lookup
    flow.create_event = fake.create_event
    flow.delete_event = fake.delete_event
    flow.availability_provider = lambda: "Ali and Salem both have slots free today."
    flow.now_provider = lambda: FROZEN_NOW
    flow._sessions.pop(device, None)

    problems: list[str] = []
    replies: list[str] = []
    for text in scenario.pre_turns + scenario.turns:
        result = flow.handle_turn(device, text)
        replies.append(f"  user : {text}\n  sarjy: {result.reply}")

    session = flow._sessions[device]
    exp = scenario.expect
    if len(fake.created) != exp.created:
        problems.append(f"expected {exp.created} created events, got {len(fake.created)}")
    if exp.created_start and fake.created:
        got = fake.created[-1]["start"].isoformat()
        if not got.startswith(exp.created_start):
            problems.append(f"expected start {exp.created_start}, got {got}")
    if exp.created_barber and fake.created:
        if fake.created[-1]["barber"] != exp.created_barber:
            problems.append(
                f"expected barber {exp.created_barber}, got {fake.created[-1]['barber']}"
            )
    if len(fake.deleted) != exp.deleted:
        problems.append(f"expected {exp.deleted} deletions, got {len(fake.deleted)}")
    if exp.final_state and session.state != exp.final_state:
        problems.append(f"expected state {exp.final_state}, got {session.state}")
    if exp.booking_discarded and session.booking.missing() != [
        "service",
        "barber",
        "day",
        "start",
        "name",
        "phone",
    ]:
        problems.append(f"expected empty booking, got {session.booking.summary()}")

    return (not problems, problems + replies, fake)
