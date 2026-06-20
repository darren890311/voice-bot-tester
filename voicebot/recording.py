"""Save the recorded call audio to a compressed file (mp3, ogg fallback).

The AudioBufferProcessor hands us raw interleaved 16-bit PCM. We reshape it to
stereo (track 0 = agent / incoming, track 1 = our patient bot) and encode with
libsndfile via soundfile — no system ffmpeg needed.
"""

import os

import numpy as np
import soundfile as sf
from loguru import logger


def save_recording(
    pcm: bytes,
    *,
    sample_rate: int,
    num_channels: int,
    out_dir: str,
    call_index: int,
    scenario_key: str,
) -> str | None:
    if not pcm:
        logger.warning("No audio captured for this call; skipping recording file.")
        return None

    os.makedirs(out_dir, exist_ok=True)
    samples = np.frombuffer(pcm, dtype=np.int16)
    if num_channels > 1:
        # Drop any trailing partial frame, then reshape to (frames, channels).
        usable = (len(samples) // num_channels) * num_channels
        samples = samples[:usable].reshape(-1, num_channels)

    base = os.path.join(out_dir, f"call-{call_index:02d}-{scenario_key}")

    # Prefer mp3; fall back to ogg/vorbis if the mp3 encoder is unavailable.
    for ext, fmt, subtype in (("mp3", "MP3", None), ("ogg", "OGG", "VORBIS")):
        path = f"{base}.{ext}"
        try:
            if subtype:
                sf.write(path, samples, sample_rate, format=fmt, subtype=subtype)
            else:
                sf.write(path, samples, sample_rate, format=fmt)
            return path
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not write {ext}: {e}")
    return None
