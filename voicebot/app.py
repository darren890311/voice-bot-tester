"""FastAPI app that bridges Twilio Media Streams to the Pipecat bot.

Flow:
  1. The runner places an outbound call (caller.py) and registers, keyed by the
     Twilio call SID, which scenario that call should run.
  2. Twilio dials the test line. When answered, it opens a WebSocket to /ws and
     streams audio. The first "start" message carries the streamSid + callSid.
  3. We look up the scenario for that callSid and run the bot to completion.
  4. On completion we save the transcript; the recording is saved inside run_bot.
"""

import json
import os

from fastapi import FastAPI, WebSocket
from loguru import logger

from .bot import run_bot
from .config import Settings
from .scenarios import Scenario

app = FastAPI()

# Populated by the runner before each call: call_sid -> (Scenario, out paths...)
PENDING: dict[str, dict] = {}
# Queue of scenarios for calls whose SID we don't yet know (used as a fallback).
FALLBACK: list[dict] = []

settings: Settings | None = None


def register_call(call_sid: str, job: dict) -> None:
    PENDING[call_sid] = job


def get_settings() -> Settings:
    global settings
    if settings is None:
        settings = Settings.load()
    return settings


@app.get("/health")
async def health():
    return {"ok": True}


@app.websocket("/ws")
async def media_stream(websocket: WebSocket):
    await websocket.accept()

    # Twilio sends a "connected" frame, then a "start" frame with the IDs.
    messages = websocket.iter_text()
    await messages.__anext__()  # "connected"
    start = json.loads(await messages.__anext__())  # "start"
    stream_sid = start["start"]["streamSid"]
    call_sid = start["start"]["callSid"]
    logger.info(f"Media stream started: call={call_sid} stream={stream_sid}")

    job = PENDING.pop(call_sid, None) or (FALLBACK.pop(0) if FALLBACK else None)
    if job is None:
        logger.error(f"No scenario registered for call {call_sid}; closing.")
        await websocket.close()
        return

    scenario: Scenario = job["scenario"]
    transcript = await run_bot(
        websocket,
        stream_sid=stream_sid,
        call_sid=call_sid,
        scenario=scenario,
        settings=get_settings(),
        out_dir=job["recordings_dir"],
        call_index=job["call_index"],
    )

    # Write the transcript next to the recording.
    os.makedirs(job["transcripts_dir"], exist_ok=True)
    path = os.path.join(
        job["transcripts_dir"],
        f"call-{job['call_index']:02d}-{scenario.key}.txt",
    )
    header = (
        f"Scenario: {scenario.label} ({scenario.key})\n"
        f"Call SID: {call_sid}\n"
        f"AGENT = Pretty Good AI test line | PATIENT = our voice bot"
    )
    with open(path, "w") as f:
        f.write(transcript.render(header=header))
    logger.info(f"Saved transcript -> {path}")

    # Signal the runner (via the job dict) that this call is done.
    job["transcript"] = transcript
    job["done"].set()
