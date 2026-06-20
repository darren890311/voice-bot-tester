# Voice Bot Patient Simulator

An automated voice bot that **calls the Pretty Good AI test line
(+1-805-439-8008)**, role-plays realistic patients (scheduling, refills,
insurance questions, edge cases), holds a natural spoken conversation, records
and transcribes both sides, and drafts a bug report on the agent's behavior.

Built with **Pipecat** (voice orchestration) + **Twilio** (telephony) +
**Deepgram** (speech-to-text) + **Claude** (the patient brain & analysis) +
**Cartesia** (text-to-speech).

## How it works (30 seconds)

```
Twilio call  ─►  Deepgram STT  ─►  Claude (patient persona)  ─►  Cartesia TTS  ─►  Twilio
                       │                                                  │
                       └────────────  transcript + stereo recording  ─────┘
```

We place an outbound call to the test line via Twilio. Twilio streams the live
audio to a local FastAPI WebSocket (exposed with ngrok), where a Pipecat
pipeline runs the loop: transcribe the agent → let Claude decide what the
"patient" says next → speak it back. Each scenario is a persona prompt that
makes Claude **actively steer** toward a specific test goal. See
[docs/architecture.md](docs/architecture.md) for design choices.

## Setup

You need accounts/keys for: **Twilio** (a voice-capable number),
**Anthropic**, **Deepgram**, **Cartesia**, and a free **ngrok** authtoken.

```bash
make setup                 # creates .venv and installs requirements.txt
cp .env.example .env       # then fill in your keys
```

Required `.env` values are documented in [.env.example](.env.example).

## Run

```bash
make list                       # show all scenario keys
make call SCENARIO=refill       # place ONE call with the "refill" patient
make all                        # run every scenario once + write BUG_REPORT.md
```

Each call:
- places an outbound call to **+1-805-439-8008** (the only number this bot dials),
- saves the recording to `recordings/call-NN-<scenario>.mp3`,
- saves a timestamped transcript to `transcripts/call-NN-<scenario>.txt`.

`make all` runs all 11 scenarios back-to-back (comfortably ≥10 calls), then
generates a first-draft `BUG_REPORT.md` from the transcripts.

To regenerate the bug report from existing transcripts without calling:

```bash
make report
```

## Scenarios

11 patient personas across the required categories plus edge cases:
scheduling, rescheduling, cancelling, refills, hours/location, insurance, and
edge cases (weekend-booking trap, vague requests, interruptions/barge-in,
multi-intent calls, off-topic recovery). They live in
[voicebot/scenarios.py](voicebot/scenarios.py) — add your own by appending to
`SCENARIOS`.

## Project layout

```
voicebot/
  __main__.py    single-command runner (ngrok + server + place calls + save)
  app.py         FastAPI WebSocket endpoint bridging Twilio <-> Pipecat
  bot.py         the Pipecat pipeline (STT -> Claude -> TTS) for one call
  caller.py      places the outbound Twilio call (locked to the test number)
  scenarios.py   patient personas / test goals
  transcript.py  timestamped two-sided transcript capture
  recording.py   encodes the call audio to mp3 (ogg fallback)
  config.py      env / settings
analysis/
  analyze.py     transcripts -> draft bug report via Claude
recordings/      *.mp3 call audio (committed for submission)
transcripts/     *.txt transcripts (committed for submission)
```

## Notes & cost

- The bot **waits for the agent to greet first** (it's an inbound call from the
  agent's side), then responds — matching real outbound-call behavior.
- Typical call is 1–3 minutes. 10–15 calls land around **$3–6** total across
  Twilio + Deepgram + Cartesia + Claude — well under the $20 guideline.
- Secrets live only in `.env` (gitignored). Never commit keys.
