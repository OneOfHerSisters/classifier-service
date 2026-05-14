import logging
import subprocess
import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = "dima806/music_genres_classification"
SAMPLE_RATE = 16000
MAX_DURATION = 30
MAX_TRACK_DURATION = 9 * 60

_pipeline = None


def _load_pipeline():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    from transformers import pipeline
    logger.info("loading model %s", MODEL_NAME)
    _pipeline = pipeline("audio-classification", model=MODEL_NAME)
    return _pipeline


def _duration(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def _load_audio(path, start_sec=None):
    if start_sec is None:
        dur = _duration(path)
        start_sec = int(max((dur - MAX_DURATION) / 2, 0)) if dur else 0

    proc = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start_sec), "-i", path,
         "-t", str(MAX_DURATION), "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "-loglevel", "error", "pipe:1"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode()}")

    return {"array": np.frombuffer(proc.stdout, dtype=np.float32), "sampling_rate": SAMPLE_RATE}


def classify_audio(path, start_sec=None):
    dur = _duration(path)
    if dur and dur > MAX_TRACK_DURATION:
        return None
    pipe = _load_pipeline()
    results = pipe(_load_audio(path, start_sec), top_k=None)
    return {r["label"].lower(): round(float(r["score"]), 4) for r in results}
