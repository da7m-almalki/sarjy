"""The orchestrator: a state machine that owns the booking, all validation, and
every side effect. LLMs extract and verbalize; this module decides.

Per turn: extract the user's message into a TurnExtract, apply it to the session
(merge fields, switch states), re-validate the whole booking against real
availability, perform any side effect (create or cancel a calendar event), then
hand converse a plain-language situation report to say out loud.

Calendar access and the clock are injected (module attributes) so the eval suite
can run against a fake calendar at a frozen time."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage

from app import calendar_client, memory
from app.booking import Booking, BusyLookup, validate
from app.llm import ConverseDeps, TurnExtract, converse, extract, run_with_retry
from app.shop import TZ

# injection points, replaced by the eval suite
busy_lookup = lambda barber, start, end: calendar_client.busy_blocks(  # noqa: E731
    calendar_client.BARBERS[barber], start, end
)
create_event = calendar_client.create_event
delete_event = calendar_client.delete_event
availability_provider = calendar_client.availability_text
now_provider = lambda: datetime.now(TZ)  # noqa: E731

HISTORY_LIMIT = 40


@dataclass
class Session:
    state: str = "chatting"  # chatting | collecting | confirming | cancelling
    booking: Booking = field(default_factory=Booking)
    cancel_candidate: dict | None = None
    reschedule_target: dict | None = None  # the old booking row while moving it
    history: list[ModelMessage] = field(default_factory=list)


_sessions: dict[str, Session] = {}


class TurnResult(BaseModel):
    reply: str
    state: str
    booking: dict


def _pretty(value: object) -> str:
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, date):
        return value.strftime("%A %B %-d")
    return str(value)


def _merge(booking: Booking, ext: TurnExtract) -> tuple[list[str], list[str]]:
    """Copy extracted fields onto the booking. The extractor's strings are untrusted
    input: a date like 2026-02-30 must be rejected here, not crash the turn. Returns
    (notes for fields that changed from one value to another, values that do not exist
    and were not applied)."""
    updates: dict[str, object] = {}
    impossible: list[str] = []
    if ext.service:
        updates["service"] = ext.service
    if ext.barber:
        updates["barber"] = ext.barber
    if ext.day:
        try:
            updates["day"] = date.fromisoformat(ext.day)
        except ValueError:
            impossible.append(f"the date '{ext.day}' does not exist")
    if ext.time:
        try:
            hh, mm = ext.time.split(":")
            updates["start"] = time(int(hh), int(mm))
        except ValueError:
            impossible.append(f"the time '{ext.time}' does not exist")
    if ext.name:
        updates["name"] = ext.name
    if ext.phone:
        updates["phone"] = ext.phone
    changes = []
    labels = {"day": "date", "start": "time"}
    for field_name, value in updates.items():
        old = getattr(booking, field_name)
        if old != value:
            setattr(booking, field_name, value)
            if old is not None:
                label = labels.get(field_name, field_name)
                changes.append(f"{label} changed from {_pretty(old)} to {_pretty(value)}")
    return changes, impossible


def _busy_for(session: Session) -> BusyLookup:
    """The busy lookup to validate against. While moving an existing appointment, its
    own calendar block must not count as taken, or a move overlapping the old slot
    would be refused because of the very event being moved."""
    target = session.reschedule_target
    if target is None:
        return busy_lookup
    old_start = datetime.fromisoformat(target["start_iso"])
    old_end = old_start + timedelta(minutes=Booking(service=target["service"]).minutes or 30)

    def lookup(barber: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        blocks = busy_lookup(barber, start, end)
        if barber == target["barber"]:
            blocks = [b for b in blocks if not (b[0] == old_start and b[1] == old_end)]
        return blocks

    return lookup


def _situation_for_booking(session: Session, now: datetime) -> str:
    """Validate the current booking and produce the ground-truth situation text."""
    booking = session.booking
    problem = validate(booking, _busy_for(session), now)
    if problem:
        text = f"Problem with the booking ({booking.summary()}): {problem.detail}"
        if problem.alternatives:
            text += " Free alternatives: " + "; ".join(problem.alternatives) + "."
        text += " Ask the customer how to proceed."
        session.state = "collecting"
        return text
    missing = booking.missing()
    if missing:
        session.state = "collecting"
        pretty = {"day": "date", "start": "time"}
        asks = ", ".join(pretty.get(m, m) for m in missing)
        return (
            f"Booking so far: {booking.summary()}. Everything stated is valid and the "
            f"slot is free so far. Still needed: {asks}. Ask for the next one naturally."
        )
    session.state = "confirming"
    return (
        f"All details collected and the slot is confirmed free: {booking.summary()}, "
        f"phone {booking.phone}. Read the summary back and ask them to confirm."
    )


def handle_turn(device_id: str, text: str) -> TurnResult:
    now = now_provider()
    session = _sessions.setdefault(device_id, Session())
    profile = memory.get_profile(device_id)

    context = (
        f"Current date and time: {now.strftime('%A %Y-%m-%d %H:%M')} (Riyadh).\n"
        f"Conversation state: {session.state}. Booking so far: {session.booking.summary()}.\n"
        f"User message: {text}"
    )
    ext: TurnExtract = run_with_retry(extract, context).output

    # persist memory-worthy details regardless of the flow
    memory.update_profile(
        device_id,
        name=ext.name or "",
        phone=ext.phone or "",
        preferred_barber=ext.preferred_barber or "",
    )
    if ext.facts:
        memory.add_facts(device_id, ext.facts)

    situation = _dispatch(session, ext, device_id, profile, now)

    deps = ConverseDeps(
        profile=memory.get_profile(device_id),
        facts=memory.get_facts(device_id),
        situation=situation,
    )
    result = run_with_retry(converse, text, deps=deps, message_history=session.history)
    session.history = result.all_messages()[-HISTORY_LIMIT:]

    return TurnResult(
        reply=result.output,
        state=session.state,
        booking=session.booking.model_dump(mode="json"),
    )


def _dispatch(
    session: Session, ext: TurnExtract, device_id: str, profile: dict, now: datetime
) -> str:
    booking = session.booking

    if ext.intent == "abandon" and session.state in ("collecting", "confirming"):
        session.booking = Booking()
        session.state = "chatting"
        if session.reschedule_target:
            session.reschedule_target = None
            return "They called off the move. The original appointment stays as it was. Confirm."
        return "The customer called off the booking. Confirm it is discarded, no hard feelings."

    if session.state == "cancelling":
        # a repeated "cancel it" while we are asking IS the confirmation
        if ext.intent in ("confirm", "cancel_existing") and session.cancel_candidate:
            target = session.cancel_candidate
            delete_event(target["barber"], target["event_id"])
            memory.cancel_booking(target["id"])
            session.cancel_candidate = None
            session.state = "chatting"
            session.booking = Booking()
            return "DONE: the booking is cancelled and removed from the calendar. Confirm that."
        if ext.intent == "deny":
            session.cancel_candidate = None
            session.state = "chatting"
            return "They decided to keep the booking. Acknowledge."

    if ext.intent == "cancel_existing":
        if session.reschedule_target:  # cancelling outright supersedes the move
            session.reschedule_target = None
            session.booking = Booking()
        upcoming = memory.upcoming_bookings(device_id, now.isoformat())
        if not upcoming:
            session.state = "chatting"
            return "They want to cancel, but they have no upcoming bookings on record. Say so."
        target = upcoming[0]
        session.cancel_candidate = target
        session.state = "cancelling"
        start = datetime.fromisoformat(target["start_iso"])
        return (
            f"They want to cancel. Found their booking: {target['service']} with "
            f"{target['barber']} on {start.strftime('%A %B %-d at %H:%M')}. "
            "NOT cancelled yet: ask them to confirm cancelling it."
        )

    if ext.intent == "reschedule":
        upcoming = memory.upcoming_bookings(device_id, now.isoformat())
        if not upcoming:
            session.state = "chatting"
            return "They want to move a booking, but they have no upcoming bookings on record."
        target = upcoming[0]
        session.reschedule_target = target
        old_start = datetime.fromisoformat(target["start_iso"])
        session.booking = Booking(
            service=target["service"],
            barber=target["barber"],
            day=old_start.date(),
            start=old_start.time(),
            name=profile.get("name") or None,
            phone=profile.get("phone") or None,
        )
        changes, impossible = _merge(session.booking, ext)
        if not changes:  # no new day or time given yet: ask instead of re-proposing the old one
            session.booking.start = None
        prefix = (
            f"They are moving their existing {target['service']} with {target['barber']} "
            f"on {old_start.strftime('%A %B %-d at %H:%M')}. The old appointment stays "
            "until they confirm a new slot. "
        )
        if impossible:
            prefix += (
                "They gave a value that does not exist ("
                + "; ".join(impossible)
                + "). Point that out and ask for a real one. "
            )
        return prefix + _situation_for_booking(session, now)

    if session.state == "confirming" and ext.intent == "confirm":
        problem = validate(booking, _busy_for(session), now)
        if problem is None and not booking.missing():
            start_at = booking.start_dt()
            assert start_at is not None and booking.minutes is not None
            assert booking.barber and booking.service and booking.name and booking.phone
            event_id = create_event(
                booking.barber,
                start_at,
                booking.minutes,
                summary=f"{booking.service}: {booking.name}",
                description=f"Booked by Sarjy. Phone: {booking.phone}",
            )
            memory.add_booking(
                device_id, booking.barber, booking.service, start_at.isoformat(), event_id
            )
            memory.update_profile(device_id, last_service=booking.service)
            done = booking.summary()
            session.booking = Booking()
            session.state = "chatting"
            old = session.reschedule_target
            if old:  # the new slot exists now, so removing the old one cannot strand them
                delete_event(old["barber"], old["event_id"])
                memory.cancel_booking(old["id"])
                session.reschedule_target = None
                return (
                    f"MOVED. The appointment is now {done} and the old slot is off the "
                    "calendar. Confirm it warmly and briefly."
                )
            return (
                f"BOOKED. The appointment ({done}) is now on the calendar. "
                "Confirm it warmly and briefly."
            )
        # something changed under us (or a field was invalidated): fall through to re-validate

    if session.state == "confirming" and ext.intent == "deny":
        session.state = "collecting"
        return "They rejected the summary. Ask what they would like to change."

    if ext.intent in ("booking_info", "confirm", "deny"):
        changes, impossible = _merge(booking, ext)
        if booking.name is None and profile.get("name"):
            booking.name = profile["name"]
        if booking.phone is None and profile.get("phone"):
            booking.phone = profile["phone"]
        prefix = ""
        if impossible:
            prefix += (
                "They gave a value that does not exist ("
                + "; ".join(impossible)
                + "). Point that out and ask for a real one. "
            )
        if changes:
            prefix += "They changed their mind: " + "; ".join(changes) + ". Acknowledge that. "
        return prefix + _situation_for_booking(session, now)

    # unrelated chatter: leave the booking untouched, steer back if one is pending
    if session.state in ("collecting", "confirming"):
        return (
            "Off-topic question mid-booking. Answer it from what you know, then steer "
            f"back to the booking ({session.booking.summary()}, still needs "
            f"{', '.join(session.booking.missing()) or 'confirmation'})."
        )
    return (
        "Ordinary conversation. Help them, and mention you can book appointments. "
        f"Live availability if asked: {availability_provider()}"
    )
