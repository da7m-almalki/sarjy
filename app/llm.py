"""The two LLM roles per turn. The extractor reads the user's message into a typed
object (booking fields, intent, memory-worthy details). Converse turns the state
machine's situation report into natural speech. Neither one touches the calendar
or the database, and neither one decides whether a booking is possible."""

import time as time_module
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, RunContext
from pydantic_ai.capabilities import Thinking
from pydantic_ai.exceptions import ModelHTTPError

from app.config import settings
from app.shop import menu_text

RETRYABLE = (429, 503)


def run_with_retry(agent: "Agent[Any, Any]", prompt: str, **kwargs: Any) -> Any:
    """Retry Gemini's transient errors with growing pauses, then give up loudly.
    503 demand spikes can outlast a single quick retry."""
    for pause in (1.5, 4.0, None):
        try:
            return agent.run_sync(prompt, **kwargs)
        except ModelHTTPError as e:
            if e.status_code in RETRYABLE and pause is not None:
                time_module.sleep(pause)
                continue
            raise
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- converse


@dataclass
class ConverseDeps:
    profile: dict[str, str] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)
    situation: str = ""


# converse only verbalizes a situation the state machine already decided; with the
# default dynamic thinking, ~90% of its output tokens went to deliberation it has
# no use for. The extractor keeps dynamic thinking (date math, intent judgment).
converse = Agent(settings.llm_model, deps_type=ConverseDeps, capabilities=[Thinking("minimal")])


@converse.instructions
def converse_instructions(ctx: RunContext[ConverseDeps]) -> str:
    parts = [
        "You are Sarjy, the voice assistant of Sarj Barbershop. "
        "You are spoken aloud, so keep replies short and natural, one or two sentences, "
        "plain punctuation only (no dashes, no bullet points). "
        "The SITUATION below is the ground truth from the booking system. Relay it "
        "faithfully: never contradict it, never invent availability, and never say a "
        "booking or cancellation is done unless the SITUATION explicitly says it is done. "
        "If it says something is not done yet, your reply must not claim it happened. "
        "If it lists alternatives, offer them.",
        menu_text(),
    ]
    if ctx.deps.situation:
        parts.append(f"SITUATION: {ctx.deps.situation}")
    if ctx.deps.profile:
        known = ", ".join(f"{k}: {v}" for k, v in ctx.deps.profile.items())
        parts.append(f"What you know about this customer: {known}")
    if ctx.deps.facts:
        parts.append("Things they told you before: " + "; ".join(ctx.deps.facts))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- extractor


class TurnExtract(BaseModel):
    """Everything machine-readable in one user message."""

    intent: Literal[
        "booking_info",  # user is providing or changing booking details, or asking to book
        "confirm",  # user agrees to what was just proposed
        "deny",  # user rejects what was just proposed
        "abandon",  # user gives up on the booking in progress
        "cancel_existing",  # user wants to cancel an already made booking
        "reschedule",  # user wants to move an already made booking
        "unrelated",  # anything else: questions, chit chat
    ]
    service: Literal["haircut", "beard trim", "haircut and beard", "kids cut"] | None = None
    barber: Literal["Ali", "Salem"] | None = None
    day: str | None = None  # ISO date, resolved from relative words using today's date
    time: str | None = None  # 24h HH:MM
    name: str | None = None
    phone: str | None = None
    preferred_barber: Literal["Ali", "Salem"] | None = None
    facts: list[str] = []


extract = Agent(
    settings.llm_model,
    output_type=NativeOutput(TurnExtract),
    instructions=(
        "You read one user message in a barbershop booking conversation and fill the schema. "
        "Only extract what THIS message says or clearly implies given the assistant's last "
        "question; leave everything else null. Fill service/barber/day/time only when the "
        "user is stating or changing what they want booked. A QUESTION about a service, "
        "barber, price, duration, or availability fills nothing and is intent=unrelated "
        "('how long does a beard trim take?' changes no fields). "
        "The context may include the assistant's last reply, exactly what the customer just "
        "heard. Use it ONLY to resolve what the user points at: 'the first one', 'the Salem "
        "one', 'the earlier one' fill the barber and time of that option, in the order the "
        "reply states them. If the reference is ambiguous ('that works' with several options "
        "on offer), fill nothing. The reply is never itself a source of new fields, and a "
        "question still fills nothing. "
        "Resolve relative dates (today, tomorrow, "
        "Friday) to ISO dates using the current date given in the message context. "
        "Times become 24h HH:MM; assume PM for ambiguous small hours like 'at 5'. "
        "intent rules: booking_info when they provide or change any booking detail or ask "
        "to book; confirm/deny only as answers to a proposal; abandon when they call off "
        "the booking in progress; cancel_existing when they want to cancel an appointment "
        "they already finished booking; reschedule when they want to move or change an "
        "appointment they already finished booking (changing a detail of the booking still "
        "being put together is booking_info, not reschedule), and a reschedule message often "
        "carries the new day or time, so fill those fields too; unrelated otherwise. "
        "Memory: set preferred_barber only on an explicit stated preference, never from a "
        "question or one booking. facts collects durable personal details worth remembering "
        "for future visits, as short third-person statements like 'favorite color is blue'. "
        "Leave facts empty for ordinary booking chatter."
    ),
)
