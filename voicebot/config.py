"""Configuration loaded from environment variables (.env)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# The assessment test line. Hard-coded as a safety rail: this bot must ONLY ever
# dial this number. See caller.py where it is enforced.
TEST_NUMBER = "+18054398008"


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


@dataclass
class Settings:
    # Telephony
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_number: str  # your Twilio number, E.164

    # AI services
    anthropic_api_key: str
    deepgram_api_key: str
    cartesia_api_key: str

    # Models / voices (sane defaults; override via env if you like)
    anthropic_model: str
    cartesia_voice_id: str
    deepgram_model: str

    # Server
    port: int

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            twilio_account_sid=_require("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_require("TWILIO_AUTH_TOKEN"),
            twilio_from_number=_require("TWILIO_FROM_NUMBER"),
            anthropic_api_key=_require("ANTHROPIC_API_KEY"),
            deepgram_api_key=_require("DEEPGRAM_API_KEY"),
            cartesia_api_key=_require("CARTESIA_API_KEY"),
            # Haiku for low reply latency — the patient is a persona simulator,
            # so first-token speed matters more than Sonnet's extra reasoning.
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            # A natural, conversational Cartesia voice. Override with any voice id.
            cartesia_voice_id=os.getenv(
                "CARTESIA_VOICE_ID", "71a7ad14-091c-4e8e-a314-022ece01c121"
            ),
            deepgram_model=os.getenv("DEEPGRAM_MODEL", "nova-3"),
            port=int(os.getenv("PORT", "8765")),
        )
