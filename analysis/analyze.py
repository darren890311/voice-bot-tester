"""Post-call analysis: read transcripts and draft a bug report with Claude.

This is a *first pass* to surface candidate issues — you should review and edit
it by hand. The model is told to prioritize a few useful, well-described bugs
over a long list of nitpicks, matching the challenge's stated preference.
"""

import glob
import os

from anthropic import Anthropic
from loguru import logger

ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "claude-opus-4-8")

SYSTEM = """You are a meticulous QA engineer reviewing transcripts of calls made
to a medical practice's AI phone agent. In each transcript, AGENT is the AI under
test and PATIENT is our automated tester.

Find real bugs and quality issues in the AGENT's behavior. Examples of real bugs:
- Confirming appointments on days/times the practice is closed
- Inventing or "finding" appointments/records that were never established
- Committing to actions it shouldn't (e.g. approving a refill a doctor must authorize)
- Dropping one of several requests in a multi-intent call
- Contradicting itself about hours, location, or insurance
- Hallucinated specifics, broken turn-taking, failure to handle interruptions or
  ambiguity, dead ends, or unsafe medical guidance.

Prioritize a SMALL number of USEFUL, well-described issues over a long list of
nitpicks. Ignore punctuation and minor phrasing. For each issue use this format:

Bug: <one line>
Severity: <High | Medium | Low>
Call: <transcript filename> at <m:ss>
Details: <what happened, why it's a problem, and what the agent should have done>

If a transcript shows no real issues, say so briefly for that call."""


def _read_transcripts(transcripts_dir: str) -> list[tuple[str, str]]:
    paths = sorted(glob.glob(os.path.join(transcripts_dir, "*.txt")))
    return [(os.path.basename(p), open(p).read()) for p in paths]


def analyze_calls(completed: list[dict], settings, out_dir: str) -> str:
    """`completed` is unused beyond locating dirs; we read transcript files on disk."""
    transcripts_dir = os.path.join(out_dir, "transcripts")
    return analyze_dir(transcripts_dir, settings.anthropic_api_key, out_dir)


def analyze_dir(transcripts_dir: str, api_key: str, out_dir: str) -> str:
    transcripts = _read_transcripts(transcripts_dir)
    if not transcripts:
        logger.warning("No transcripts found to analyze.")
        return ""

    blob = "\n\n".join(
        f"===== {name} =====\n{text}" for name, text in transcripts
    )
    client = Anthropic(api_key=api_key)
    message = client.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=4000,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Here are {len(transcripts)} call transcripts. "
                f"Produce the bug report.\n\n{blob}",
            }
        ],
    )
    report = "".join(b.text for b in message.content if b.type == "text")

    path = os.path.join(out_dir, "BUG_REPORT.md")
    with open(path, "w") as f:
        f.write("# Bug Report\n\n")
        f.write("_Draft generated from call transcripts; reviewed/edited by hand._\n\n")
        f.write(report + "\n")
    logger.info(f"Wrote bug report -> {path}")
    return report


if __name__ == "__main__":
    # Re-run analysis on the most recent run's transcripts without placing calls:
    #   python -m analysis.analyze
    from dotenv import load_dotenv

    load_dotenv()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runs = sorted(d for d in glob.glob(os.path.join(root, "runs", "*")) if os.path.isdir(d))
    if not runs:
        logger.error("No runs found under runs/. Place a call first (make call ...).")
        raise SystemExit(1)
    latest = runs[-1]  # timestamped names sort chronologically
    logger.info(f"Analyzing latest run: {latest}")
    analyze_dir(
        os.path.join(latest, "transcripts"),
        os.environ["ANTHROPIC_API_KEY"],
        latest,
    )
