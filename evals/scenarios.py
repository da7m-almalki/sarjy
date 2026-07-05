"""The scripted conversations. Clean runs plus one or more variations per
off-script category from the PRD. Frozen clock: Saturday 2026-07-04 10:00 Riyadh."""

from datetime import datetime

from app.shop import TZ
from evals.harness import Expect, Scenario


def _busy(barber_blocks: dict[str, list[tuple[str, str]]]) -> dict:
    """{'Ali': [('2026-07-05 17:00', '2026-07-05 18:00')]} with Riyadh times."""
    out: dict[str, list[tuple[datetime, datetime]]] = {"Ali": [], "Salem": []}
    for barber, blocks in barber_blocks.items():
        for start, end in blocks:
            out[barber].append(
                (
                    datetime.fromisoformat(start).replace(tzinfo=TZ),
                    datetime.fromisoformat(end).replace(tzinfo=TZ),
                )
            )
    return out


SCENARIOS = [
    # ---- clean runs
    Scenario(
        name="one shot booking",
        turns=[
            "hi, i am Khalid, phone 0501234567, book me a haircut with Ali tomorrow at 5pm",
            "yes, confirm it",
        ],
        expect=Expect(
            created=1,
            created_start="2026-07-05T17:00",
            created_barber="Ali",
            final_state="chatting",
            booking_discarded=True,
        ),
    ),
    Scenario(
        name="incremental booking",
        turns=[
            "i want to book a haircut",
            "with Salem",
            "tomorrow at noon",
            "my name is Omar and my phone is 0559876543",
            "yes that is right",
        ],
        expect=Expect(
            created=1,
            created_start="2026-07-05T12:00",
            created_barber="Salem",
            final_state="chatting",
        ),
    ),
    # ---- changed answers re-validate downstream
    Scenario(
        name="change time mid flow",
        recovery=True,
        turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "actually make it 7pm instead",
            "yes, book it",
        ],
        expect=Expect(created=1, created_start="2026-07-05T19:00", created_barber="Ali"),
    ),
    Scenario(
        name="change day revalidates the held slot",
        recovery=True,
        busy=_busy({"Ali": [("2026-07-06 17:00", "2026-07-06 18:00")]}),
        turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "wait, monday works better for me, same time",
            "ok 6pm then",
            "yes confirm",
        ],
        expect=Expect(created=1, created_start="2026-07-06T18:00", created_barber="Ali"),
    ),
    # ---- added service changes duration and breaks the fit
    Scenario(
        name="added service no longer fits",
        recovery=True,
        busy=_busy({"Ali": [("2026-07-05 17:30", "2026-07-05 18:00")]}),
        turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "actually make it a haircut and beard",
            "fine, 6pm works",
            "yes book it",
        ],
        expect=Expect(created=1, created_start="2026-07-05T18:00", created_barber="Ali"),
    ),
    # ---- impossible asks get honesty plus alternatives
    Scenario(
        name="closed friday",
        recovery=True,
        turns=[
            "khalid, 0501234567, i want a haircut with Ali on friday at 5pm",
            "saturday at 5pm then",
            "yes",
        ],
        expect=Expect(created=1, created_start="2026-07-11T17:00", created_barber="Ali"),
    ),
    Scenario(
        name="taken slot offers alternatives",
        recovery=True,
        busy=_busy({"Ali": [("2026-07-05 17:00", "2026-07-05 17:30")]}),
        turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "ok the salem option at 5 works",
            "confirm",
        ],
        expect=Expect(created=1, created_start="2026-07-05T17:00", created_barber="Salem"),
    ),
    # ---- a question probing a specific slot is a tentative proposal
    Scenario(
        name="slot probe question starts the booking",
        recovery=True,
        turns=[
            "hey, is 5pm tomorrow free for a haircut with Ali?",
            "great, book it, i am Khalid and my number is 0501234567",
            "yes",
        ],
        expect=Expect(
            created=1,
            created_start="2026-07-05T17:00",
            created_barber="Ali",
            final_state="chatting",
        ),
    ),
    # ---- a cut-off phone number fails validation, not just the read-back
    Scenario(
        name="truncated phone number is rejected",
        recovery=True,
        turns=[
            "khalid, haircut with Ali tomorrow at 5pm, my number is 0501234",
            "oh sorry, it is 0501234567",
            "yes book it",
        ],
        expect=Expect(created=1, created_start="2026-07-05T17:00", created_barber="Ali"),
    ),
    # ---- picking an offered alternative by reference, not by restating it
    Scenario(
        name="picks an alternative by reference",
        recovery=True,
        busy=_busy({"Ali": [("2026-07-05 17:00", "2026-07-05 17:30")]}),
        turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "the first one",
            "yes",
        ],
        # alternatives are computed nearest-first; 16:45 does not fit a 30 minute
        # haircut before the 17:00 block, so: Ali 16:30, Ali 17:30, Salem 17:00
        expect=Expect(created=1, created_start="2026-07-05T16:30", created_barber="Ali"),
    ),
    # ---- detours never lose collected state
    Scenario(
        name="detour keeps state",
        recovery=True,
        turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "by the way, my favorite color is blue, remember that",
            "how long does a kids cut take?",
            "great, confirm my booking",
        ],
        expect=Expect(created=1, created_start="2026-07-05T17:00", created_barber="Ali"),
    ),
    # ---- abandonment discards cleanly
    Scenario(
        name="abandon discards",
        recovery=True,
        turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "you know what, forget the whole thing",
        ],
        expect=Expect(created=0, final_state="chatting", booking_discarded=True),
    ),
    # ---- cross-session memory: "the usual" resolves from booking history
    Scenario(
        name="the usual books the remembered service",
        pre_bookings=[
            {
                "barber": "Ali",
                "service": "haircut and beard",
                "start_iso": "2026-06-20T17:00:00+03:00",
            },
        ],
        turns=[
            "hey, it's khalid, 0501234567, book me the usual tomorrow at 5pm",
            "yes book it",
        ],
        expect=Expect(
            created=1,
            created_start="2026-07-05T17:00",
            created_barber="Ali",
            final_state="chatting",
        ),
    ),
    # ---- cancel an existing booking
    Scenario(
        name="cancel existing booking",
        recovery=True,
        pre_turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "yes book it",
        ],
        turns=[
            "i need to cancel my appointment",
            "yes cancel it",
        ],
        expect=Expect(created=1, deleted=1, final_state="chatting"),
    ),
    # ---- reschedule an existing booking: new event created, old one deleted
    Scenario(
        name="reschedule moves the booking",
        recovery=True,
        pre_turns=[
            "khalid, 0501234567, haircut with Ali tomorrow at 5pm",
            "yes book it",
        ],
        turns=[
            "can i move my appointment to 8pm instead?",
            "yes",
        ],
        expect=Expect(
            created=2,
            created_start="2026-07-05T20:00",
            created_barber="Ali",
            deleted=1,
            final_state="chatting",
            booking_discarded=True,
        ),
    ),
]
