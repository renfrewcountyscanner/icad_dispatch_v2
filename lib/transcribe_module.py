# lib/transcribe_module.py
from __future__ import annotations

import io
import json
import logging
import mimetypes
import os
import re
from typing import Any, BinaryIO, Dict, Iterable, List, Optional, Tuple

import requests
from openai import OpenAI
from pydub import AudioSegment

module_logger = logging.getLogger("icad_dispatch.transcribe_module")

_DEFAULT_CHUNK_SECONDS = 8 * 60
_MAX_PROMPT_HINTS = 12
_MAX_PROMPT_LENGTH = 1200


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


def _audio_format_from_filename(name: str | None) -> str:
    ext = os.path.splitext((name or "").strip())[1].lower().lstrip(".")
    if ext in {"wav", "mp3", "m4a", "mp4", "aac", "ogg", "flac", "webm", "oga"}:
        return ext
    return "wav"


def _clean_hint(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _unique_hints(values: Iterable[Any], *, limit: int = _MAX_PROMPT_HINTS) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_hint(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _collect_prompt_hints(transcribe_config: dict) -> List[str]:
    hints: List[str] = []
    hints.extend([
        transcribe_config.get("system_name"),
        transcribe_config.get("talkgroup_name"),
        f"Talkgroup {transcribe_config['talkgroup']}" if transcribe_config.get("talkgroup") not in (None, "") else None,
    ])
    hints.extend(transcribe_config.get("fired_trigger_names") or [])
    hints.extend(transcribe_config.get("trigger_names") or [])
    hints.extend(transcribe_config.get("location_hints") or [])
    return _unique_hints(hints)


def _build_prompt(transcribe_config: dict) -> Optional[str]:
    configured = _clean_hint(transcribe_config.get("prompt"))
    hints = _collect_prompt_hints(transcribe_config)

    parts = [
        "You are transcribing public safety radio audio.",
        "Preserve exact agency, department, station, township, road, unit, and call-sign names when they are spoken.",
        "Prefer the local names in the hint list over generic substitutes.",
    ]

    if hints:
        parts.append("Known local names: " + ", ".join(f'"{h}"' for h in hints))

    if configured:
        parts.append("User guidance: " + configured)
    else:
        parts.append("Use punctuation sparingly and do not invent missing names.")

    prompt = "\n".join(parts).strip()
    if len(prompt) > _MAX_PROMPT_LENGTH:
        prompt = prompt[:_MAX_PROMPT_LENGTH].rstrip()
    return prompt or None


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
        try:
            bio.name = send_name  # type: ignore[attr-defined]
        except Exception:
            pass
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


def _load_audio_segment(audio: Any, filename: str | None) -> AudioSegment:
    fmt = _audio_format_from_filename(filename)
    if isinstance(audio, (bytes, bytearray, memoryview)):
        return AudioSegment.from_file(io.BytesIO(bytes(audio)), format=fmt)
    if isinstance(audio, (str, os.PathLike)):
        return AudioSegment.from_file(str(audio), format=fmt)
    if hasattr(audio, "read"):
        try:
            pos = audio.tell()
        except Exception:
            pos = None
        try:
            data = audio.read()
        finally:
            if pos is not None:
                try:
                    audio.seek(pos)
                except Exception:
                    pass
        return AudioSegment.from_file(io.BytesIO(data), format=fmt)
    raise TypeError("audio must be bytes, file-like, or a path-like string")


def _transcribe_single_payload(
        *,
        audio,
        filename: str | None,
        transcribe_config: dict,
        timeout: float,
) -> dict:
    api_key = transcribe_config["api_key"]
    model = transcribe_config["model"]
    base_url = transcribe_config.get("url")
    language = transcribe_config.get("language")
    prompt = _build_prompt(transcribe_config)

    endpoint, is_official = _qualify_transcribe_endpoint(base_url)

    send_name, file_obj, mime, needs_close = _wrap_uploadable(audio, filename)
    try:
        if is_official:
            client = OpenAI(api_key=api_key)

            if not getattr(file_obj, "name", None):
                try:
                    setattr(file_obj, "name", send_name)
                except Exception:
                    pass

            kwargs = {
                "model": model,
                "file": file_obj,
                "response_format": "verbose_json",
                "timestamp_granularities": ["word"],
            }
            if language:
                kwargs["language"] = language
            if prompt:
                kwargs["prompt"] = prompt

            resp = client.audio.transcriptions.create(**kwargs)
            try:
                return resp.model_dump(exclude_none=True)
            except Exception:
                return json.loads(resp.model_dump_json(exclude_none=True))

        data: list[tuple[str, Any]] = [
            ("model", model),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "word"),
        ]
        if language:
            data.append(("language", str(language)))
        if prompt:
            data.append(("prompt", str(prompt)))

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


def _merge_response_chunks(chunks: List[Tuple[float, Dict[str, Any]]], duration_s: float) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    texts: List[str] = []
    segments: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []

    for offset_s, resp in chunks:
        if not isinstance(resp, dict):
            continue
        text = (resp.get("text") or "").strip()
        if text:
            texts.append(text)

        for seg in resp.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            seg_copy = dict(seg)
            seg_copy["start"] = float(seg_copy.get("start") or 0.0) + offset_s
            seg_copy["end"] = float(seg_copy.get("end") or seg_copy["start"]) + offset_s

            seg_words = seg_copy.get("words")
            if isinstance(seg_words, list):
                new_words: List[Dict[str, Any]] = []
                for word in seg_words:
                    if not isinstance(word, dict):
                        continue
                    word_copy = dict(word)
                    word_copy["start"] = float(word_copy.get("start") or 0.0) + offset_s
                    word_copy["end"] = float(word_copy.get("end") or word_copy["start"]) + offset_s
                    new_words.append(word_copy)
                    words.append(word_copy)
                seg_copy["words"] = new_words

            segments.append(seg_copy)

    merged["text"] = " ".join(texts).strip()
    if segments:
        merged["segments"] = segments
    if words:
        merged["words"] = words
    merged["duration"] = float(duration_s)
    return merged


def _transcribe_chunked(
        *,
        audio_segment: AudioSegment,
        filename: str | None,
        transcribe_config: dict,
        timeout: float,
        chunk_seconds: int,
) -> dict:
    chunk_ms = max(1, int(chunk_seconds)) * 1000
    chunks: List[Tuple[float, Dict[str, Any]]] = []

    for offset_ms in range(0, len(audio_segment), chunk_ms):
        part = audio_segment[offset_ms:offset_ms + chunk_ms]
        if not len(part):
            continue
        chunk_bytes = io.BytesIO()
        part.export(chunk_bytes, format="wav")
        resp = _transcribe_single_payload(
            audio=chunk_bytes.getvalue(),
            filename=filename,
            transcribe_config=transcribe_config,
            timeout=timeout,
        )
        chunks.append((offset_ms / 1000.0, resp))

    if not chunks:
        return {"text": "", "segments": [], "words": [], "duration": 0.0}

    return _merge_response_chunks(chunks, duration_s=len(audio_segment) / 1000.0)


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
    chunk_seconds = int(transcribe_config.get("chunk_seconds") or _DEFAULT_CHUNK_SECONDS)
    if chunk_seconds > 0:
        try:
            audio_segment = _load_audio_segment(audio, filename)
            if len(audio_segment) > (chunk_seconds * 1000):
                module_logger.info(
                    "Chunking transcribe audio: duration_s=%.2f chunk_seconds=%d",
                    len(audio_segment) / 1000.0,
                    chunk_seconds,
                )
                return _transcribe_chunked(
                    audio_segment=audio_segment,
                    filename=filename,
                    transcribe_config=transcribe_config,
                    timeout=timeout,
                    chunk_seconds=chunk_seconds,
                )
        except Exception as exc:
            module_logger.warning("Chunking skipped; falling back to single request: %s", exc)

    return _transcribe_single_payload(
        audio=audio,
        filename=filename,
        transcribe_config=transcribe_config,
        timeout=timeout,
    )
