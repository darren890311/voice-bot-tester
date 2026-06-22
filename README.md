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

Each invocation writes into its own run folder (timestamp + scenario, e.g.
`runs/<timestamp>-refill/`) so reruns never overwrite previous evidence and each
folder is easy to identify. Each call:
- places an outbound call to **+1-805-439-8008** (the only number this bot dials),
- saves the recording to `runs/<timestamp>-<scenario>/recordings/call-NN-<scenario>.mp3`,
- saves the transcript to `runs/<timestamp>-<scenario>/transcripts/call-NN-<scenario>.txt`.

`make all` runs all 10 scenarios back-to-back (meets the ≥10-call bar), then
generates a first-draft `BUG_REPORT.md` inside that run folder (the folder is
tagged `-all`).

To regenerate the bug report from the most recent run's transcripts without calling:

```bash
make report
```

## Scenarios

10 patient personas across the required categories plus edge cases:
scheduling, rescheduling, cancelling, refills, hours/location, insurance, and
edge cases (weekend-booking trap, vague requests, interruptions/barge-in, and a
controlled-substance early-refill safety test). They live in
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
runs/            <timestamp>-<scenario>/{recordings/*.mp3, transcripts/*.txt, BUG_REPORT.md}
                 one folder per run (pick one to submit)
```

## Notes & cost

- The bot **waits for the agent to greet first** (it's an inbound call from the
  agent's side), then responds — matching real outbound-call behavior.
- **Audio is telephony-narrowband by design, not a bug.** Twilio Media Streams
  deliver G.711 µ-law **8 kHz** in both directions, so recordings are narrowband —
  there is no way to capture wideband through this path. (Calling the same agent
  directly from a mobile can sound clearer because that leg may be HD Voice /
  wideband; our recording leg is fixed at 8 kHz.) We still get the cleanest 8 kHz
  we can: TTS is generated at 24 kHz and downsampled, and the patient's level is
  attenuated for headroom so it never clips.
- Typical call is 1–3 minutes. 10–15 calls land around **$3–6** total across
  Twilio + Deepgram + Cartesia + Claude — well under the $20 guideline.
- Secrets live only in `.env` (gitignored). Never commit keys.
