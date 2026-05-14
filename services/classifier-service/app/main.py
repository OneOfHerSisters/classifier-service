import asyncio
import base64
import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from google.cloud import firestore, storage

from .model import _load_pipeline, classify_audio

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
SONGS_COLLECTION = os.getenv("SONGS_COLLECTION", "songs")
SCORES_COLLECTION = os.getenv("SCORES_COLLECTION", "audio_genre_scores_dev")

_db = None
_gcs = None


def _get_db():
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def _get_gcs():
    global _gcs
    if _gcs is None:
        _gcs = storage.Client()
    return _gcs


def _download_gcs(gs_uri, local_path):
    path = gs_uri[len("gs://"):]
    bucket_name, blob_name = path.split("/", 1)
    _get_gcs().bucket(bucket_name).blob(blob_name).download_to_filename(local_path)


def _save_results(song_id, genre_scores):
    top_tags = sorted(genre_scores, key=genre_scores.get, reverse=True)[:3]
    _get_db().collection(SONGS_COLLECTION).document(song_id).set({
        "tags": top_tags,
        "status": "ready",
        "classified_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    _get_db().collection(SCORES_COLLECTION).document(song_id).set({
        "genre_scores": genre_scores,
        "classified_at": firestore.SERVER_TIMESTAMP,
    })


@asynccontextmanager
async def lifespan(app):
    _load_pipeline()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict")
async def predict(body: dict):
    instances = body.get("instances")
    if not instances:
        raise HTTPException(400, "'instances' is required")

    predictions = []
    loop = asyncio.get_running_loop()
    timeout = httpx.Timeout(120.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for instance in instances:
            url = instance.get("gcs_signed_url")
            if not url:
                raise HTTPException(400, "each instance needs 'gcs_signed_url'")

            start_sec = instance.get("start_sec")
            if start_sec is not None and start_sec < 0:
                raise HTTPException(400, "'start_sec' must be >= 0")

            ext = os.path.splitext(url.split("?")[0])[-1] or ".mp3"
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            tmp.close()
            try:
                downloaded = 0
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(tmp.name, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            downloaded += len(chunk)
                            if downloaded > MAX_DOWNLOAD_BYTES:
                                raise HTTPException(413, "audio file is too large")
                            f.write(chunk)

                scores = await loop.run_in_executor(
                    None, classify_audio, tmp.name, start_sec
                )
                if scores is None:
                    predictions.append({"genre": "none"})
                else:
                    top_genre = max(scores, key=scores.get)
                    predictions.append({"genre": top_genre})
            except httpx.HTTPError as e:
                raise HTTPException(502, f"download failed: {e}")
            finally:
                try:
                    os.unlink(tmp.name)
                except FileNotFoundError:
                    pass

    return {"predictions": predictions}


@app.post("/pubsub/classify")
async def pubsub_classify(body: dict):
    song_id = None
    try:
        payload = json.loads(base64.b64decode(body["message"]["data"]))
        song_id = payload["song_id"]
        storage_path = payload["storage_path"]
        start_sec = payload.get("start_sec")

        ext = os.path.splitext(storage_path)[-1] or ".mp3"
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        tmp.close()
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _download_gcs, storage_path, tmp.name)
            scores = await loop.run_in_executor(None, classify_audio, tmp.name, start_sec)
        finally:
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass

        genre_scores = scores if scores is not None else {"none": 1.0}
        _save_results(song_id, genre_scores)
        return {"status": "ok", "song_id": song_id}

    except Exception as e:
        logger.exception("pubsub classification failed, song_id=%s", song_id)
        # return 200 so Pub/Sub doesn't redeliver poison messages
        return {"status": "error", "detail": str(e)}
