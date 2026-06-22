"""Save the recorded call audio to a compressed file (mp3, ogg fallback).

The AudioBufferProcessor hands us raw interleaved 16-bit PCM. We reshape it to
stereo (track 0 = agent / incoming, track 1 = our patient bot) and encode with
libsndfile via soundfile — no system ffmpeg needed.
"""

import os

import numpy as np
import soundfile as sf
from loguru import logger


_FULL_SCALE = 32768.0


def _log_levels(samples: np.ndarray, num_channels: int) -> None:
    """Log per-channel peak / clip stats on raw PCM (pre-encode ground truth)."""
    chans = samples.reshape(-1, 1) if samples.ndim == 1 else samples
    # Track 0 = agent/incoming, track 1 = our patient bot (see bot.py audio path).
    names = ["agent", "patient"] if num_channels == 2 else [f"ch{i}" for i in range(chans.shape[1])]
    for i, name in enumerate(names[: chans.shape[1]]):
        ch = chans[:, i].astype(np.int32)
        peak = int(np.abs(ch).max()) if ch.size else 0
        peak_dbfs = 20 * np.log10(peak / _FULL_SCALE) if peak else float("-inf")
        # Hard clipping = samples pinned at full scale (int16 saturates at -32768).
        clipped = int(np.count_nonzero((ch >= 32767) | (ch <= -32768)))
        clip_pct = 100 * clipped / ch.size if ch.size else 0.0
        flag = "  <-- CLIPPING" if clip_pct > 0.0 else ""
        logger.info(
            f"audio level [{name}]: peak={peak} ({peak_dbfs:+.2f} dBFS) "
            f"clipped={clipped} ({clip_pct:.3f}%){flag}"
        )


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

    # Ground-truth level check on the RAW PCM, before any lossy encode rounds the
    # peaks off and hides clipping. Hard digital clipping shows as samples pinned
    # at full scale; a healthy signal peaks a few dB below it.
    _log_levels(samples, num_channels)

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
