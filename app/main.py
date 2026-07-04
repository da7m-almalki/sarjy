from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage

from app import memory
from app.calendar_client import availability_text
from app.config import settings
from app.llm import ConverseDeps, converse, memory_extract
from app.shop import BARBERS

app = FastAPI(title="Sarjy")

STATIC = Path(__file__).parent.parent / "static"

# per-process conversation history; cross-session memory lives in SQLite
_histories: dict[str, list[ModelMessage]] = {}
HISTORY_LIMIT = 40


class ChatRequest(BaseModel):
    device_id: str
    text: str


class ChatResponse(BaseModel):
    reply: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/meta")
def meta() -> dict:
    return {"barbers": {name: cal_id for name, cal_id in BARBERS.items()}}


@app.post("/chat")
def chat(req: ChatRequest) -> ChatResponse:
    deps = ConverseDeps(
        profile=memory.get_profile(req.device_id),
        facts=memory.get_facts(req.device_id),
        availability=availability_text(),
    )
    history = _histories.get(req.device_id, [])

    result = converse.run_sync(req.text, deps=deps, message_history=history)
    _histories[req.device_id] = result.all_messages()[-HISTORY_LIMIT:]

    update = memory_extract.run_sync(req.text).output
    memory.update_profile(
        req.device_id,
        name=update.name or "",
        phone=update.phone or "",
        preferred_barber=update.preferred_barber or "",
    )
    if update.facts:
        memory.add_facts(req.device_id, update.facts)

    return ChatResponse(reply=result.output)


@app.get("/tts")
def tts(text: str) -> StreamingResponse:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}/stream"

    # open the upstream stream before responding, so an ElevenLabs error
    # becomes a proper 502 instead of a silent empty 200
    stream = httpx.stream(
        "POST",
        url,
        headers={"xi-api-key": settings.elevenlabs_api_key},
        json={"text": text, "model_id": settings.elevenlabs_tts_model},
        timeout=30,
    )
    response = stream.__enter__()
    if response.status_code != 200:
        detail = response.read().decode(errors="replace")[:200]
        stream.__exit__(None, None, None)
        raise HTTPException(status_code=502, detail=f"TTS failed: {detail}")

    def audio():
        try:
            yield from response.iter_bytes()
        finally:
            stream.__exit__(None, None, None)

    return StreamingResponse(audio(), media_type="audio/mpeg")
