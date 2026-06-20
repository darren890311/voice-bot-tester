"""Places the outbound Twilio call that connects to our media-stream server."""

from loguru import logger
from twilio.rest import Client

from .config import TEST_NUMBER, Settings


def _twiml(ws_url: str) -> str:
    """TwiML that streams the call's audio (both ways) to our WebSocket."""
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<Response>"
        f"<Connect><Stream url='{ws_url}' /></Connect>"
        "</Response>"
    )


def place_call(settings: Settings, public_host: str) -> str:
    """Dial the test line. Returns the Twilio call SID.

    Safety rail: we ONLY ever dial the assessment number.
    """
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    ws_url = f"wss://{public_host}/ws"

    call = client.calls.create(
        to=TEST_NUMBER,
        from_=settings.twilio_from_number,
        twiml=_twiml(ws_url),
        # Dual-channel recording is captured locally by the bot; this is a backup
        # at the carrier level and also gives clean call timing/status.
        record=False,
    )
    logger.info(f"Placed call {call.sid} -> {TEST_NUMBER} (stream {ws_url})")
    return call.sid
