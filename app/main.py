import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app import flow
from app.config import settings
from app.shop import BARBERS

app = FastAPI(title="Sarjy")
log = logging.getLogger("sarjy")

STATIC = Path(__file__).parent.parent / "static"


class ChatRequest(BaseModel):
    device_id: str
    text: str


class ChatResponse(BaseModel):
    reply: str
    state: str = "chatting"
    booking: dict = {}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/meta")
def meta() -> dict:
    return {"barbers": {name: cal_id for name, cal_id in BARBERS.items()}}


@app.post("/chat")
def chat(req: ChatRequest) -> ChatResponse:
    try:
        result = flow.handle_turn(req.device_id, req.text)
    except Exception:
        # LLM or calendar hiccup: reply something speakable instead of a raw 500
        log.exception("turn failed")
        return ChatResponse(
            reply="Sorry, I hit a snag on my end. Give me a second and say that again."
        )
    return ChatResponse(reply=result.reply, state=result.state, booking=result.booking)


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
