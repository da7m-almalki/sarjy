# Sarjy PRD

Voice assistant that books appointments at a fictional barbershop. Deep dive: multistep workflows (state keeping and off-script recovery in the booking flow).

## Minimum bar

- Voice in and out in the browser.
- Remembers users across sessions (name, phone, preferred barber, plus free-form facts). Identity without login: the page stores a random device ID in localStorage and sends it with every request; all memory is keyed by that ID server-side. Per browser, so a new device or incognito window is a new user.
- External API: Google Calendar. Availability checks and bookings run against real calendars.
- Deployed at a public URL, no login.

Why Google Calendar: a booking assistant is only convincing if the booking lands somewhere real. Google Calendar is where a small business actually keeps its schedule, and the demo calendars are public, so anyone can verify that a spoken conversation produced a correct event.

## The barbershop

Closed world, defined in config:

- Barbers: Ali and Salem. One Google Calendar each, public.
- Services: haircut 30 min, beard trim 15 min, haircut + beard 45 min, kids cut 20 min.
- Hours: Sat to Thu, 10:00 to 22:00, Asia/Riyadh. Closed Friday.
- Calendars are pre-seeded with bookings so conflicts happen naturally.

## Deep dive

A booking is a typed Pydantic object: service, barber, date, time, name, phone. Each field has validation (service must be on the menu, time must fit the full duration inside a free window, and so on). Conversation states are explicit: chatting, collecting, confirming, booked, cancelling.

The LLM converses and extracts fields. The Python state machine owns the state, runs all validation, and is the only component that calls the calendar. The model never decides that a slot is free.

Off-script means anything that breaks the straight path: changed answers, added requests mid-booking, impossible asks, unrelated detours, abandonment. The general rule is the same for all of them: fields changed by the user re-trigger validation of everything that depends on them, invalid requests get an honest explanation plus computed alternatives, and detours never lose collected state.

Measured with a suite of scripted conversations, clean runs plus off-script variations. Reported: task success rate, recovery rate, turns to completion.

## Stack

- Python, FastAPI, uv.
- PydanticAI for LLM calls with validated structured output.
- LLM: Gemini Flash. Provider is a config string, easy to swap.
- STT: browser Web Speech API (English, works best in Chrome). Fallback if accuracy disappoints: Deepgram streaming.
- TTS: ElevenLabs Flash v2.5, streamed from the backend. Fallback: browser speechSynthesis.
- Memory: SQLite, keyed by the device ID. Profile fields, free-form facts, and confirmed bookings with their calendar event IDs. Loaded into the system prompt at session start; no vector store at this scale.
- Calendar access: service account, key in env vars.
- Frontend: a single static page served by FastAPI. Mic button, transcript, a panel showing the booking fields as they fill, embedded public calendar.
- Deploy: Render, single service.

## Plan

1. Day 1: this PRD, repo, draft PR, account setup (Google Cloud, Gemini key, ElevenLabs). Voice loop working end to end.
2. Day 2: state machine, calendar integration, memory. Deploy.
3. Day 3: recovery cases, eval suite, writeup, Loom, presentation.
