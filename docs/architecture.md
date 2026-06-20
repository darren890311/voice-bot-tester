# Architecture

The system places an outbound phone call to the test line and runs a real-time
voice loop in which Claude plays a patient. A Twilio call's audio is streamed
over a WebSocket (Twilio Media Streams) to a local FastAPI server exposed via
ngrok. That WebSocket feeds a **Pipecat** pipeline:
`Twilio → Deepgram STT → Claude → Cartesia TTS → Twilio`, with a transcript
logger and a stereo audio recorder tapped into the same stream. Each "patient"
is a persona system prompt (in `scenarios.py`) that gives Claude a concrete goal
and rules for talking like a real caller, so the bot **actively steers** the
conversation rather than answering passively. The runner (`python -m voicebot`)
brings up the server and tunnel, places calls one at a time, and on each call's
completion writes a timestamped transcript (`transcripts/`) and an mp3
(`recordings/`); an optional pass sends all transcripts to Claude to draft a
bug report.

**Key design choices.** I chose a *pipeline* (separate STT/LLM/TTS) over a
single speech-to-speech model because the task is fundamentally about *control*:
I want a strong reasoning model driving the persona and steering toward each test
outcome, full transcripts I can analyze, and the freedom to tune endpointing and
voice independently — Pipecat gives that while still handling the hard real-time
parts (VAD, barge-in, turn-taking) that the rubric grades first. Telephony audio
is kept at 8 kHz end-to-end to avoid needless resampling and latency. The bot
deliberately **does not speak first** — on an outbound call the answering agent
greets, and waiting for that greeting (rather than scripting an opener
immediately) is what makes the exchange feel natural and tests the agent's real
opening behavior. Claude's max output tokens are capped low so replies stay
short and phone-like instead of monologuing. Recording uses libsndfile via
`soundfile` (bundled in the wheel) so the project produces mp3/ogg with no system
ffmpeg dependency. Finally, the outbound number is hard-coded to the assessment
line as a safety rail — the bot can only ever dial +1-805-439-8008.
