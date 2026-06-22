"""Timestamped, two-sided transcript capture.

Pipecat 1.4 has no built-in transcript processor, so we capture the
conversation ourselves. The catch: no single point in the pipeline sees both
sides. The user aggregator consumes ``TranscriptionFrame`` and never pushes it
downstream (see ``LLMUserAggregator._handle_transcription``), so the agent's
words only exist on the ``stt -> aggregator.user()`` segment. Our bot's words
(``TTSTextFrame``) only exist from ``tts`` downstream. So we tap two points and
feed them into one shared collector:

  - AGENT  : final speech-to-text of the line we called (TranscriptionFrame),
             tapped between STT and the user aggregator
  - PATIENT: what our own bot said out loud (aggregated TTSTextFrame),
             tapped after TTS

Timestamps are formatted m:ss so the bug report can cite "transcript-07.txt at 1:23".
"""

import time
from dataclasses import dataclass

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.string import TextPartForConcatenation, concatenate_aggregated_text


@dataclass
class Turn:
    speaker: str  # "AGENT" or "PATIENT"
    text: str
    t: float  # seconds since call start


class TranscriptLogger:
    """Shared collector for conversation turns.

    Not a pipeline processor itself: it owns the turn buffer and the call's
    start time, and hands out two thin ``FrameProcessor`` views — one for each
    point in the pipeline where the relevant frames are visible. Insert
    :meth:`agent_view` between STT and the user aggregator, and
    :meth:`patient_view` after TTS.
    """

    def __init__(self) -> None:
        self._start = time.monotonic()
        self.turns: list[Turn] = []
        self._patient_buf: list[TextPartForConcatenation] = []
        self._agent_buf: list[str] = []
        # Timestamp of the first final in the current agent turn, so the merged
        # turn is cited at when the agent started talking, not when we flush it.
        self._agent_t: float | None = None

    def _elapsed(self) -> float:
        return time.monotonic() - self._start

    def _add_agent(self, text: str) -> None:
        # Deepgram emits several finals per spoken turn; buffer them and merge on
        # flush so one agent utterance is one line. Genuine re-prompts (the agent
        # actually repeating itself) stay visible as repeated text in the turn.
        if not self._agent_buf:
            self._agent_t = self._elapsed()
        self._agent_buf.append(text)

    def _flush_agent(self) -> None:
        text = " ".join(self._agent_buf).strip()
        self._agent_buf.clear()
        if text:
            self.turns.append(Turn("AGENT", text, self._agent_t or self._elapsed()))
        self._agent_t = None

    def _buffer_patient(self, text: str, includes_inter_frame_spaces: bool) -> None:
        # Cartesia streams word-level TTSTextFrames with no inter-frame spaces,
        # so "".join() ran words together ("I'dliketoschedule"). Track each
        # frame's spacing flag and let Pipecat's joiner reinsert spaces while
        # leaving punctuation and already-spaced runs intact.
        self._patient_buf.append(TextPartForConcatenation(text, includes_inter_frame_spaces))

    def _flush_patient(self) -> None:
        # The agent turn always precedes the patient's reply, so close it out
        # first: this is the agent->patient boundary that ends an agent turn.
        self._flush_agent()
        text = concatenate_aggregated_text(self._patient_buf)
        self._patient_buf.clear()
        if text:
            self.turns.append(Turn("PATIENT", text, self._elapsed()))

    def agent_view(self) -> "FrameProcessor":
        return _AgentTap(self)

    def patient_view(self) -> "FrameProcessor":
        return _PatientTap(self)

    def finalize(self) -> None:
        self._flush_patient()

    def render(self, header: str = "") -> str:
        self.finalize()
        # Turns are appended from two processors running concurrently; sort by
        # timestamp so the rendered conversation reads in chronological order.
        turns = sorted(self.turns, key=lambda turn: turn.t)
        lines = []
        if header:
            lines.append(header)
            lines.append("")
        for turn in turns:
            m, s = divmod(int(turn.t), 60)
            lines.append(f"[{m}:{s:02d}] {turn.speaker}: {turn.text}")
        return "\n".join(lines) + "\n"

    def as_messages(self) -> list[dict]:
        """Plain transcript for the post-call analyzer."""
        self.finalize()
        turns = sorted(self.turns, key=lambda turn: turn.t)
        return [{"speaker": t.speaker, "text": t.text, "t": t.t} for t in turns]


class _AgentTap(FrameProcessor):
    """Captures the agent's finalized transcriptions (TranscriptionFrame).

    Must sit upstream of the user aggregator, which consumes those frames.
    """

    def __init__(self, collector: TranscriptLogger) -> None:
        super().__init__()
        self._collector = collector

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._collector._add_agent(frame.text.strip())
        elif isinstance(frame, UserStoppedSpeakingFrame):
            # A real agent turn just ended (the aggregator broadcasts this
            # upstream once turn detection fires). Flush now so each agent turn
            # is its own line, instead of merging consecutive agent turns — e.g.
            # a recorded "this call may be recorded" disclaimer and the live
            # greeting — into one line just because we didn't reply between them.
            self._collector._flush_agent()

        await self.push_frame(frame, direction)


class _PatientTap(FrameProcessor):
    """Captures our bot's speech (TTSTextFrame), tapped after TTS.

    TTS text is streamed token-by-token; buffer it until the bot stops speaking.
    """

    def __init__(self, collector: TranscriptLogger) -> None:
        super().__init__()
        self._collector = collector

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSTextFrame):
            self._collector._buffer_patient(frame.text, frame.includes_inter_frame_spaces)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._collector._flush_patient()

        await self.push_frame(frame, direction)
