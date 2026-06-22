# Architecture

## Overview

The system places an outbound phone call to the test line and runs a real-time
voice loop in which Claude plays a patient. A Twilio call's audio is streamed over
a WebSocket (Twilio Media Streams) to a local FastAPI server exposed via ngrok.
That WebSocket feeds a **Pipecat** pipeline, with a transcript logger and a stereo
audio recorder tapped into the same stream:

```
Twilio  ─►  Deepgram STT  ─►  Claude (patient persona)  ─►  Cartesia TTS  ─►  Twilio
                  │                                                   │
                  └───────────────  transcript + stereo recording  ───┘
```

Each "patient" is a persona system prompt (in [`scenarios.py`](../voicebot/scenarios.py))
that gives Claude a concrete goal and rules for talking like a real caller, so the
bot **actively steers** the conversation rather than answering passively. The
runner (`python -m voicebot`) brings up the server and tunnel, places calls one at
a time, and on each call's completion writes a transcript and an mp3 into a per-run
folder (`runs/<timestamp>-<scenario>/{transcripts,recordings}/`); an optional pass
sends all transcripts to Claude to draft a bug report in the same folder.

## Key design choices

- **Pipeline (separate STT / LLM / TTS), not a single speech-to-speech model.**
  The task is fundamentally about *control*: a model driving the persona and
  steering toward each test outcome, full transcripts I can analyze, and the
  freedom to tune endpointing and voice independently. Pipecat gives that while
  still handling the hard real-time parts — VAD, barge-in, turn-taking — that the
  rubric grades first.

- **Semantic turn-taking.** Turn-end is decided by Silero VAD *plus* a smart-turn
  model (`LocalSmartTurnAnalyzerV3`), not a fixed silence timeout, so the bot
  doesn't reply to mid-sentence pauses. VAD `stop_secs` is tuned to span this
  agent's inter-sentence gaps; smart-turn's own timeout is trimmed to cut stalls.

- **Fast patient model.** The patient runs on **Claude Haiku** rather than a
  heavier model: steering a persona is not reasoning-heavy, and on a live phone
  call first-token latency matters far more — a quicker reply feels more natural
  and avoids awkward dead air. (The offline bug-report analysis can use a stronger
  model.)

- **Audio: 8 kHz on the wire, but TTS generated at 24 kHz.** Twilio Media Streams
  are G.711 µ-law 8 kHz in both directions, so the call and recording are
  narrowband by design. Cartesia's *native* 8 kHz output sounds gritty/aliased,
  so we generate TTS at 24 kHz and let the output transport downsample to 8 kHz —
  the cleanest 8 kHz we can get. The patient's level is also attenuated for
  headroom so it never clips.

- **The bot does not speak first.** On an outbound call the answering agent
  greets; waiting for that greeting (rather than scripting an opener immediately)
  is what makes the exchange feel natural and tests the agent's real opening
  behavior.

- **Short, phone-like replies.** Claude's max output tokens are capped low so the
  patient stays terse instead of monologuing.

- **No system ffmpeg.** Recording uses libsndfile via `soundfile` (bundled in the
  wheel), so the project produces mp3/ogg with no external audio dependency.

- **Hard-coded outbound number.** The bot can only ever dial the assessment line
  (+1-805-439-8008) — a safety rail against accidentally calling anyone else.
