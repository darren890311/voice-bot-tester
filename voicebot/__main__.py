"""Single-command runner: bring up the server, place calls, save outputs.

Usage:
    python -m voicebot --scenario refill                 # one call
    python -m voicebot --scenario refill --scenario cancel
    python -m voicebot --all                             # one call per scenario
    python -m voicebot --all --analyze                   # also write bug report

After `setup` (see README), this is the only command you need.
"""

import argparse
import os
import threading
import time

import uvicorn
from loguru import logger
from pyngrok import conf, ngrok

from . import app as appmod
from . import scenarios
from .caller import place_call
from .config import Settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Each invocation writes into its own timestamped run folder so reruns never
# overwrite previous evidence: runs/<timestamp>/{recordings,transcripts}/ + the
# generated BUG_REPORT.md.
RUNS_DIR = os.path.join(ROOT, "runs")


def _start_server(port: int) -> uvicorn.Server:
    config = uvicorn.Config(appmod.app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.1)
    return server


def _open_tunnel(port: int) -> str:
    token = os.getenv("NGROK_AUTHTOKEN")
    if token:
        conf.get_default().auth_token = token
    tunnel = ngrok.connect(port, "http")
    host = tunnel.public_url.split("://", 1)[1]
    logger.info(f"ngrok tunnel: {tunnel.public_url} -> :{port}")
    return host


def run(scenario_keys: list[str], analyze: bool) -> None:
    settings = Settings.load()
    appmod.settings = settings

    server = _start_server(settings.port)
    public_host = _open_tunnel(settings.port)

    # One timestamped folder per run so nothing from a previous run is clobbered.
    # Tag it with the scenario (or "all") so runs are easy to tell apart at a glance.
    run_id = time.strftime("%Y%m%dT%H%M%S")
    label = scenario_keys[0] if len(scenario_keys) == 1 else "all"
    run_dir = os.path.join(RUNS_DIR, f"{run_id}-{label}")
    recordings_dir = os.path.join(run_dir, "recordings")
    transcripts_dir = os.path.join(run_dir, "transcripts")
    os.makedirs(recordings_dir, exist_ok=True)
    os.makedirs(transcripts_dir, exist_ok=True)
    logger.info(f"Run outputs -> {run_dir}")

    completed: list[dict] = []
    try:
        for i, key in enumerate(scenario_keys, start=1):
            scenario = scenarios.get(key)
            logger.info(f"[{i}/{len(scenario_keys)}] Scenario: {scenario.label}")

            job = {
                "scenario": scenario,
                "call_index": i,
                "recordings_dir": recordings_dir,
                "transcripts_dir": transcripts_dir,
                "done": threading.Event(),
            }
            call_sid = place_call(settings, public_host)
            appmod.register_call(call_sid, job)

            # Wait for this call to finish before placing the next one.
            if not job["done"].wait(timeout=360):
                logger.error(f"Call {call_sid} timed out; moving on.")
                appmod.PENDING.pop(call_sid, None)
                continue
            completed.append(job)
            time.sleep(2)  # brief breather between calls
    finally:
        ngrok.disconnect_all() if hasattr(ngrok, "disconnect_all") else ngrok.kill()
        server.should_exit = True

    logger.info(f"Done. {len(completed)} call(s) completed.")

    if analyze and completed:
        from analysis.analyze import analyze_calls

        analyze_calls(completed, settings, out_dir=run_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice bot patient simulator")
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Scenario key to run (repeatable). See scenarios.py.",
    )
    parser.add_argument("--all", action="store_true", help="Run every scenario once.")
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="After calls, generate a bug report with Claude.",
    )
    parser.add_argument("--list", action="store_true", help="List scenarios and exit.")
    args = parser.parse_args()

    if args.list:
        for key in scenarios.all_keys():
            print(f"  {key:24s} {scenarios.get(key).label}")
        return

    keys = scenarios.all_keys() if args.all else args.scenario
    if not keys:
        parser.error("Pass --scenario KEY (repeatable), or --all. Use --list to see keys.")
    unknown = [k for k in keys if k not in scenarios.all_keys()]
    if unknown:
        parser.error(
            f"Unknown scenario(s): {', '.join(unknown)}. Use --list to see keys."
        )
    run(keys, analyze=args.analyze)


if __name__ == "__main__":
    main()
