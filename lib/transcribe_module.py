# lib/transcribe_module.py
from __future__ import annotations

import io, json, os
import logging
import mimetypes
from typing import Any, Dict, List, Tuple, Optional, BinaryIO

import requests
from openai import OpenAI

module_logger = logging.getLogger("icad_dispatch.transcribe_module")

def _qualify_transcribe_endpoint(base_url: str | None) -> tuple[str, bool]:
    """
    Return (endpoint_url, is_official_openai).
    """
    if not base_url or "api.openai.com" in base_url:
        return "https://api.openai.com/v1/audio/transcriptions", True
    base = str(base_url).rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    return f"{base}/audio/transcriptions", False


def _mimetype_from_filename(name: str | None) -> str:
    mt, _ = mimetypes.guess_type(name or "")
    return mt or "application/octet-stream"

def _wrap_uploadable(audio: Any, filename: str | None) -> Tuple[str, BinaryIO, str, bool]:
    """
    Return (send_name, file_obj, mime, needs_close).
    - bytes/bytearray/memoryview → BytesIO (no close needed)
    - file-like → returned as-is (no close here)
    - str/path → open rb (we'll close after)
    """
    if isinstance(audio, (bytes, bytearray, memoryview)):
        send_name = os.path.basename(filename) if filename else "audio.wav"
        mime = _mimetype_from_filename(send_name)
        bio = io.BytesIO(bytes(audio))
        try: bio.name = send_name  # type: ignore[attr-defined]
        except Exception: pass
        return send_name, bio, mime, False

    if hasattr(audio, "read"):  # file-like
        send_name = os.path.basename(filename) if filename else getattr(audio, "name", "audio")
        mime = _mimetype_from_filename(str(send_name))
        return str(send_name), audio, mime, False

    if isinstance(audio, (str, os.PathLike)):
        path = str(audio)
        send_name = os.path.basename(filename or path)
        mime = _mimetype_from_filename(send_name)
        f = open(path, "rb")
        return send_name, f, mime, True

    raise TypeError("audio must be bytes, file-like, or a path-like string")

def transcribe_audio(
        *,
        audio,                      # bytes | file-like | path (already preprocessed)
        filename: str | None,
        transcribe_config: dict,    # drives request params only
        timeout: float = 120.0,
) -> dict:
    """
    Upload the provided audio and request verbose JSON with segment timestamps.
    No normalization or resampling is performed here.
    """
    api_key  = transcribe_config["api_key"]
    model    = transcribe_config["model"]
    base_url = transcribe_config.get("url")
    language = transcribe_config.get("language")
    prompt   = transcribe_config.get("prompt")

    endpoint, is_official = _qualify_transcribe_endpoint(base_url)

    send_name, file_obj, mime, needs_close = _wrap_uploadable(audio, filename)
    try:
        if is_official:
            client = OpenAI(api_key=api_key)

            # Ensure a name attribute when possible
            if not getattr(file_obj, "name", None):
                try: setattr(file_obj, "name", send_name)
                except Exception: pass

            kwargs = {
                "model": model,
                "file": file_obj,
                "response_format": "verbose_json",
                "timestamp_granularities": ["word"],
            }
            if language: kwargs["language"] = language
            if prompt:   kwargs["prompt"]   = prompt

            resp = client.audio.transcriptions.create(**kwargs)
            try:
                return resp.model_dump(exclude_none=True)
            except Exception:
                return json.loads(resp.model_dump_json(exclude_none=True))

        # Third-party OpenAI-compatible REST
        data: list[tuple[str, Any]] = [
            ("model", model),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "word"),
        ]
        if language: data.append(("language", str(language)))
        if prompt:   data.append(("prompt", str(prompt)))

        files = {"file": (send_name, file_obj, mime)}
        headers = {"Authorization": f"Bearer {api_key}"}

        r = requests.post(endpoint, headers=headers, data=data, files=files, timeout=timeout)
        if not (200 <= r.status_code < 300):
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise RuntimeError(f"Transcribe error {r.status_code} at {endpoint}: {detail}")

        try:
            return r.json()
        except Exception:
            return json.loads(r.text)

    finally:
        if needs_close:
            try:
                file_obj.close()
            except Exception:
                pass
