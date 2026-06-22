"""Builds and runs the Pipecat voice pipeline for a single outbound call.

Audio path (Twilio media stream <-> our bot):

    Twilio  ->  transport.input  ->  Deepgram STT  ->  user aggregator
            ->  Claude (patient brain)  ->  Cartesia TTS  ->  transcript logger
            ->  transport.output  ->  audio recorder  ->  assistant aggregator

The "patient" persona prompt makes Claude steer the conversation toward a
specific test outcome. We DON'T speak first: we wait for the agent that answers
the call to greet us, then respond — that's how a real outbound call works.
"""

import os

import numpy as np
from loguru import logger
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    EndWorkerFrame,
    Frame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMRunFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from .config import Settings
from .recording import save_recording
from .scenarios import Scenario
from .transcript import TranscriptLogger

# Telephony audio is 8 kHz; matching it everywhere avoids needless resampling.
SAMPLE_RATE = 8000


class _WaitForAgentGreeting(FrameProcessor):
    """Keeps the patient silent until the agent has actually started speaking.

    Turn detection decides *when* an agent turn is complete; this is the
    structural guarantee for the opening turn specifically. The LLM is triggered
    by an ``LLMContextFrame`` pushed from the user aggregator — we drop any such
    trigger until at least one ``UserStartedSpeakingFrame`` has gone by, so the
    patient cannot talk over or pre-empt the greeting before the agent has said a
    word. We open permanently on the agent's first word and then stay out of the
    way. This replaces relying on the system prompt's "wait for their greeting"
    wording alone.

    Keying on turn *start* (not stop) is deliberate: start is broadcast before
    the turn's inference trigger, so the agent's first completed turn still flows
    through — no deadlock.
    """

    def __init__(self) -> None:
        super().__init__()
        self._agent_started = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            self._agent_started = True

        if not self._agent_started and isinstance(frame, (LLMContextFrame, LLMRunFrame)):
            return

        await self.push_frame(frame, direction)


class _AttenuateBotAudio(FrameProcessor):
    """Scale the patient's TTS audio down to leave headroom below 0 dBFS.

    Cartesia's output peaks right at full scale, which clips on loud syllables
    ("爆音") — and because this sits ahead of ``transport.output()``, both the
    live call (what the agent hears) and the recording were overloaded. A fixed
    digital attenuation is deterministic and TTS-model-independent. Scaling
    16-bit by 0.5 costs ~1 bit (~90 dB SNR) — inaudible — but removes the
    overload. Tune via the gain once a call's pre-encode true-peak log
    (see recording.save_recording) shows the new headroom.
    """

    def __init__(self, gain: float = 0.5) -> None:
        super().__init__()
        self._gain = gain
        # Ground-truth diagnostic on the RAW TTS, measured before we scale and
        # before any lossy encode. If `clipped` is non-zero here, Cartesia is
        # clipping at the source — attenuation only makes that distortion
        # quieter, it can't undo it (the fix would be a lower generation level
        # or a higher generation rate), so we want to see it directly.
        self._raw_peak = 0
        self._raw_clipped = 0
        self._raw_total = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, OutputAudioRawFrame) and frame.audio:
            raw = np.frombuffer(frame.audio, dtype=np.int16)
            if raw.size:
                self._raw_peak = max(self._raw_peak, int(np.abs(raw.astype(np.int32)).max()))
                self._raw_clipped += int(np.count_nonzero((raw >= 32767) | (raw <= -32768)))
                self._raw_total += int(raw.size)
            samples = raw.astype(np.float32) * self._gain
            np.clip(samples, -32768, 32767, out=samples)
            frame.audio = samples.astype(np.int16).tobytes()
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._raw_total:
            dbfs = 20 * np.log10(self._raw_peak / 32768) if self._raw_peak else float("-inf")
            pct = 100 * self._raw_clipped / self._raw_total
            logger.info(
                f"raw TTS (pre-attenuation): peak={self._raw_peak} ({dbfs:+.2f} dBFS) "
                f"clipped={self._raw_clipped} ({pct:.3f}%)"
                + ("  <-- Cartesia is clipping at the source" if self._raw_clipped else "")
            )
            self._raw_peak = self._raw_clipped = self._raw_total = 0

        await self.push_frame(frame, direction)


class _HangUpAfterGoodbye(FrameProcessor):
    """End the call once the patient has spoken its closing line.

    The persona ends with a fixed line ("...thank you, goodbye." — see
    scenarios.py). Without this, nothing on our side hangs up: the agent replies
    "Goodbye" too, which is a fresh turn that makes the patient say goodbye
    again — an awkward loop that only stops when the far end hangs up or the
    5-minute timeout fires. We accumulate the current bot utterance from its
    TTSTextFrames and, once it finishes (BotStoppedSpeakingFrame) carrying the
    closing phrase, push an EndWorkerFrame downstream. EndWorkerFrame flushes
    queued frames first, so the goodbye audio still plays out fully before we end.
    """

    _CLOSING = "thank you goodbye"

    def __init__(self) -> None:
        super().__init__()
        self._utterance = ""
        self._ending = False

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSTextFrame):
            self._utterance += " " + frame.text
        elif isinstance(frame, BotStoppedSpeakingFrame) and not self._ending:
            spoken = self._normalize(self._utterance)
            self._utterance = ""
            if self._CLOSING in spoken:
                self._ending = True
                logger.info("Patient said its closing line; hanging up.")
                await self.push_frame(frame, direction)
                await self.push_frame(EndWorkerFrame(), FrameDirection.DOWNSTREAM)
                return

        await self.push_frame(frame, direction)


class _BargeInOnce(FrameProcessor):
    """Deliberately talk over the agent once, mid-sentence, to test barge-in.

    A fixed-delay timer kept missing: the agent's prompts after our opener are
    short ("Please provide your date of birth"), so by the time our injected
    audio actually played the agent had already finished — the line landed in the
    silence and overlapped nothing. Instead we watch the live transcription and
    fire the moment the agent is clearly mid-LONG-turn (>= _MIN_WORDS spoken this
    turn), which guarantees it is still talking when our audio lands. Once only.

    Placed upstream of the user aggregator so it can see the agent's interim/final
    transcriptions (the aggregator consumes them and never pushes them on); the
    injected TTSSpeakFrame still flows downstream through the aggregator's
    passthrough to the TTS.
    """

    _LINE = "Sorry — hold on one sec, can I ask something real quick?"
    _MIN_WORDS = 8  # a genuinely long turn; short prompts cap around 6-7 words

    def __init__(self) -> None:
        super().__init__()
        self._armed = False  # only after our opener, so there's a call to interrupt
        self._done = False
        self._turn_words = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._armed = True
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._turn_words = 0
        elif isinstance(frame, TranscriptionFrame):
            self._turn_words += len(frame.text.split())
        elif (
            isinstance(frame, InterimTranscriptionFrame)
            and self._armed
            and not self._done
            and self._turn_words + len(frame.text.split()) >= self._MIN_WORDS
        ):
            self._done = True
            logger.info("Barge-in: talking over the agent mid-sentence now.")
            await self.push_frame(TTSSpeakFrame(self._LINE), FrameDirection.DOWNSTREAM)

        await self.push_frame(frame, direction)


def _system_prompt(scenario: Scenario) -> str:
    return (
        scenario.persona
        + "\n\nThe agent will answer the phone and greet you first. After their "
        + f"greeting, open the conversation naturally, along the lines of: "
        + f'"{scenario.opener}"'
    )


async def run_bot(
    websocket,
    *,
    stream_sid: str,
    call_sid: str,
    scenario: Scenario,
    settings: Settings,
    out_dir: str,
    call_index: int,
) -> TranscriptLogger:
    """Run one call to completion. Returns the transcript logger."""

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        # Passing creds lets the serializer hang up the Twilio call when we end.
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=SAMPLE_RATE,
            add_wav_header=False,
            # VAD + turn detection now live on the user aggregator (Pipecat 1.4
            # moved them there); a vad_analyzer passed here is silently ignored.
            serializer=serializer,
            # Safety net: end the call if it runs away.
            session_timeout=60 * 5,
        ),
    )

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        model=settings.deepgram_model,
        sample_rate=SAMPLE_RATE,
    )
    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        sample_rate=SAMPLE_RATE,
        settings=CartesiaTTSService.Settings(voice=settings.cartesia_voice_id),
    )
    llm = AnthropicLLMService(
        api_key=settings.anthropic_api_key,
        # Cap tokens so the patient gives short, phone-natural replies.
        settings=AnthropicLLMService.Settings(
            model=settings.anthropic_model, max_tokens=150
        ),
    )

    context = LLMContext(messages=[{"role": "system", "content": _system_prompt(scenario)}])
    aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            # VAD stop_secs is the gap the agent may pause mid-turn before we
            # even consider replying. This agent speaks in many short sentences
            # with brief pauses, and smart-turn (below) can't save us here: a
            # finished sentence like "I'll save it." reads as COMPLETE even when
            # the agent is about to continue, so a short stop_secs (0.3) made us
            # talk over them. 0.8 spans those inter-sentence gaps. (It does not
            # cause the multi-second stalls — those are smart-turn's own
            # stop_secs, tuned just below.)
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.8)),
            # Semantic end-of-turn: the smart-turn model decides whether a
            # silence is a real turn end or a mid-sentence pause. Its own
            # stop_secs (default 3.0s) is how long it waits, while it keeps
            # judging INCOMPLETE, before forcing the turn closed when the agent
            # trails off — that wait was the main perceived "stall", so trim it
            # to 2.0s.
            user_turn_strategies=UserTurnStrategies(
                stop=[
                    TurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=LocalSmartTurnAnalyzerV3(
                            params=SmartTurnParams(stop_secs=2.0)
                        )
                    )
                ],
            ),
        ),
    )

    transcript = TranscriptLogger()
    audio_buffer = AudioBufferProcessor(sample_rate=SAMPLE_RATE, num_channels=2)

    # Only the barge-in scenarios deliberately talk over the agent; everyone else
    # keeps clean turn-taking. Sits right after STT so it can watch the agent's
    # live transcription and fire mid-long-turn; the TTSSpeakFrame it injects
    # flows downstream (through the aggregator's passthrough) to the TTS.
    barge_in = [_BargeInOnce()] if scenario.barge_in else []

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            *barge_in,
            # AGENT tap: must sit before the user aggregator, which consumes
            # TranscriptionFrames and never pushes them downstream.
            transcript.agent_view(),
            aggregator.user(),
            # Structurally hold the patient's first reply until the agent has
            # actually begun their greeting.
            _WaitForAgentGreeting(),
            llm,
            tts,
            # Pull the TTS level down below 0 dBFS so loud syllables don't clip
            # on the live call or in the recording. Must sit before both
            # transport.output() and the audio_buffer so both get clean audio.
            _AttenuateBotAudio(gain=0.5),
            # PATIENT tap: TTSTextFrames only exist from here downstream.
            transcript.patient_view(),
            # Hang up once the patient finishes its closing line, so we don't
            # loop goodbyes with the agent. Sits before transport.output(): it
            # reads TTSTextFrames (downstream) and BotStoppedSpeakingFrame (which
            # transport.output broadcasts upstream).
            _HangUpAfterGoodbye(),
            transport.output(),
            audio_buffer,
            aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
            enable_usage_metrics=True,
        ),
    )

    # Collect recorded audio chunks; written to disk after the call ends.
    audio_chunks: list[bytes] = []

    @audio_buffer.event_handler("on_audio_data")
    async def _on_audio(_buffer, audio: bytes, sample_rate: int, num_channels: int):
        audio_chunks.append(audio)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnect(_transport, _client):
        logger.info("Twilio client disconnected; ending task.")
        await task.queue_frames([EndFrame()])

    await audio_buffer.start_recording()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)

    await audio_buffer.stop_recording()

    # Persist audio (mp3/ogg) for the submission.
    audio_path = save_recording(
        b"".join(audio_chunks),
        sample_rate=SAMPLE_RATE,
        num_channels=2,
        out_dir=out_dir,
        call_index=call_index,
        scenario_key=scenario.key,
    )
    logger.info(f"Saved recording -> {audio_path}")

    return transcript
