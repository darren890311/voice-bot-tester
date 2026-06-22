"""Preflight: validate every credential cheaply before placing a paid call.

Run with:  python -m voicebot.preflight   (or: make check)

Each check hits a lightweight, read-only endpoint to confirm the key actually
authenticates — so a bad key fails here in a second instead of mid-call.
Secrets are never printed.
"""

import sys

import requests
from anthropic import Anthropic
from twilio.rest import Client

from .config import Settings


def _ok(name: str, detail: str = "") -> tuple[str, bool, str]:
    return (name, True, detail)


def _fail(name: str, detail: str) -> tuple[str, bool, str]:
    return (name, False, detail)


def check_twilio(s: Settings):
    try:
        client = Client(s.twilio_account_sid, s.twilio_auth_token)
        acct = client.api.v2010.accounts(s.twilio_account_sid).fetch()
        # Confirm the FROM number is actually owned + voice-capable.
        nums = client.incoming_phone_numbers.list(phone_number=s.twilio_from_number, limit=1)
        if not nums:
            return _fail(
                "Twilio",
                f"FROM number {s.twilio_from_number} not found on this account",
            )
        if not nums[0].capabilities.get("voice"):
            return _fail("Twilio", f"{s.twilio_from_number} is not voice-capable")
        return _ok("Twilio", f"account '{acct.friendly_name}' status={acct.status}")
    except Exception as e:  # noqa: BLE001
        return _fail("Twilio", str(e))


def check_anthropic(s: Settings):
    try:
        models = Anthropic(api_key=s.anthropic_api_key).models.list(limit=1)
        return _ok("Anthropic", f"key valid (model: {models.data[0].id})")
    except Exception as e:  # noqa: BLE001
        return _fail("Anthropic", str(e))


def check_deepgram(s: Settings):
    try:
        r = requests.get(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {s.deepgram_api_key}"},
            timeout=10,
        )
        if r.status_code == 200:
            return _ok("Deepgram", "key valid")
        return _fail("Deepgram", f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:  # noqa: BLE001
        return _fail("Deepgram", str(e))


def check_cartesia(s: Settings):
    try:
        r = requests.get(
            "https://api.cartesia.ai/voices",
            headers={
                "X-API-Key": s.cartesia_api_key,
                "Cartesia-Version": "2024-06-10",
            },
            timeout=10,
        )
        if r.status_code == 200:
            return _ok("Cartesia", "key valid")
        return _fail("Cartesia", f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:  # noqa: BLE001
        return _fail("Cartesia", str(e))


def check_ngrok(s: Settings):
    import os

    if not os.getenv("NGROK_AUTHTOKEN"):
        return _fail("ngrok", "NGROK_AUTHTOKEN not set in .env")
    try:
        from pyngrok import conf, ngrok

        conf.get_default().auth_token = os.environ["NGROK_AUTHTOKEN"]
        tunnel = ngrok.connect(s.port, "http")
        url = tunnel.public_url
        ngrok.disconnect(url)
        ngrok.kill()
        return _ok("ngrok", f"tunnel opened ({url})")
    except Exception as e:  # noqa: BLE001
        return _fail("ngrok", str(e))


def main() -> int:
    try:
        s = Settings.load()
    except Exception as e:  # noqa: BLE001
        print(f"✗ Config: {e}")
        return 1

    checks = [
        check_twilio(s),
        check_anthropic(s),
        check_deepgram(s),
        check_cartesia(s),
        check_ngrok(s),
    ]
    print("\nPreflight checks:")
    all_ok = True
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name:10s} {detail}")
        all_ok = all_ok and ok
    print()
    if all_ok:
        print("All green. Ready to place a call:  make call SCENARIO=schedule_simple")
        return 0
    print("Fix the ✗ items above, then re-run:  make check")
    return 1


if __name__ == "__main__":
    sys.exit(main())
