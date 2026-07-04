"""The two LLM roles. Converse talks to the user; the memory extractor silently
pulls out details worth remembering. Neither one touches the calendar or the database."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, RunContext

from app.config import settings
from app.shop import menu_text


@dataclass
class ConverseDeps:
    profile: dict[str, str] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)
    availability: str = ""


converse = Agent(settings.llm_model, deps_type=ConverseDeps)


@converse.instructions
def converse_instructions(ctx: RunContext[ConverseDeps]) -> str:
    parts = [
        "You are Sarjy, the voice assistant of Sarj Barbershop. "
        "You are spoken aloud, so keep replies short and natural, one or two sentences. "
        "Never invent availability; only state what the context below says. "
        "You cannot make, change, or cancel bookings yet. If asked to book, "
        "say booking is coming soon and do not pretend a booking happened.",
        menu_text(),
    ]
    if ctx.deps.availability:
        parts.append(f"Availability right now: {ctx.deps.availability}")
    if ctx.deps.profile:
        known = ", ".join(f"{k}: {v}" for k, v in ctx.deps.profile.items())
        parts.append(f"What you know about this customer: {known}")
    if ctx.deps.facts:
        parts.append("Things they told you before: " + "; ".join(ctx.deps.facts))
    return "\n\n".join(parts)


class MemoryUpdate(BaseModel):
    """Personal details found in the user's message, all optional."""

    name: str | None = None
    phone: str | None = None
    preferred_barber: Literal["Ali", "Salem"] | None = None
    facts: list[str] = []


memory_extract = Agent(
    settings.llm_model,
    output_type=NativeOutput(MemoryUpdate),
    instructions=(
        "Extract personal details from the user's message: their name, phone number, "
        "preferred barber, and any other durable personal fact worth "
        "remembering for future visits (preferences, favorites). "
        "Only set preferred_barber if they explicitly state a preference "
        "('I prefer Salem', 'Ali is my guy'), never from a question or a one-off booking mention. "
        "Write facts as short "
        "third-person statements, e.g. 'favorite color is blue'. "
        "Do not record anything about the current conversation flow. "
        "If there is nothing to remember, return all fields empty."
    ),
)
