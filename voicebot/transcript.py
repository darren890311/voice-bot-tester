"""Timestamped, two-sided transcript capture.

Pipecat 1.4 has no built-in transcript processor, so we drop a small
FrameProcessor into the pipeline that records, with a call-relative timestamp:

  - AGENT  : final speech-to-text of the line we called (TranscriptionFrame)
  - PATIENT: what our own bot said out loud (aggregated TTSTextFrame)

Timestamps are formatted m:ss so the bug report can cite "transcript-07.txt at 1:23".
"""

import time
from dataclasses import dataclass, field

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class Turn:
    speaker: str  # "AGENT" or "PATIENT"
    text: str
    t: float  # seconds since call start


class TranscriptLogger(FrameProcessor):
    """Collects conversation turns as frames flow through the pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self._start = time.monotonic()
        self.turns: list[Turn] = []
        self._patient_buf: list[str] = []

    def _elapsed(self) -> float:
        return time.monotonic() - self._start

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            # Incoming audio that Deepgram finalized = the AGENT talking.
            self.turns.append(Turn("AGENT", frame.text.strip(), self._elapsed()))
        elif isinstance(frame, TTSTextFrame):
            # Our bot's speech, streamed token-by-token; buffer until it stops.
            self._patient_buf.append(frame.text)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._flush_patient()

        await self.push_frame(frame, direction)

    def _flush_patient(self) -> None:
        text = "".join(self._patient_buf).strip()
        self._patient_buf.clear()
        if text:
            self.turns.append(Turn("PATIENT", text, self._elapsed()))

    def finalize(self) -> None:
        self._flush_patient()

    def render(self, header: str = "") -> str:
        self.finalize()
        lines = []
        if header:
            lines.append(header)
            lines.append("")
        for turn in self.turns:
            m, s = divmod(int(turn.t), 60)
            lines.append(f"[{m}:{s:02d}] {turn.speaker}: {turn.text}")
        return "\n".join(lines) + "\n"

    def as_messages(self) -> list[dict]:
        """Plain transcript for the post-call analyzer."""
        self.finalize()
        return [{"speaker": t.speaker, "text": t.text, "t": t.t} for t in self.turns]
