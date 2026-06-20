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

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .config import Settings
from .recording import save_recording
from .scenarios import Scenario
from .transcript import TranscriptLogger

# Telephony audio is 8 kHz; matching it everywhere avoids needless resampling.
SAMPLE_RATE = 8000


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
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.6)),
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
    aggregator = LLMContextAggregatorPair(context)

    transcript = TranscriptLogger()
    audio_buffer = AudioBufferProcessor(sample_rate=SAMPLE_RATE, num_channels=2)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregator.user(),
            llm,
            tts,
            transcript,
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
