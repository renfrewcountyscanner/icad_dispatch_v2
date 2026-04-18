# lib/audio_file_handler.py
import io
import json
import logging
import math
import os
import re
import bisect
import shutil
import subprocess
from dataclasses import dataclass
from typing import Tuple, List, Optional, Dict, Iterable, Any, Union

from pydub import AudioSegment, effects
from werkzeug.datastructures import FileStorage

module_logger = logging.getLogger("icad_dispatch.audio_file_module")

class AudioValidationError(Exception):
    """
    Raised when an uploaded audio file fails validation.

    This is the single exception type your route handlers should catch for any
    user-facing validation problems (empty upload, undecodable input, duration
    too short, conversion failure, etc.). Lower-level errors (e.g., FFmpeg
    failures) should be wrapped and re-raised as this type.
    """


class AudioConversionError(Exception):
    """
    Raised for internal FFmpeg conversion failures.

    This covers issues such as:
    - `ffmpeg` not found on PATH
    - FFmpeg failing to decode the input bytes
    - The conversion pipeline producing no output

    Notes
    -----
    Application code should usually not surface this directly to users.
    Catch it and re-raise as `AudioValidationError` instead.
    """


def bytes_to_16k_mono_s16_wav(input_bytes: bytes) -> Tuple[bytes, AudioSegment]:
    """
    Convert arbitrary audio bytes to **WAV (PCM s16le, 16 kHz, mono)** using FFmpeg.

    Parameters
    ----------
    input_bytes
        Raw uploaded audio bytes (MP3, M4A, FLAC, WAV, OGG, etc.). The entire
        payload is processed in-memory.

    Returns
    -------
    wav_bytes : bytes
        The converted audio as a WAV container with PCM 16-bit, 16 kHz, mono.
        Safe to persist or stream directly to downstream services (e.g., Whisper).
    seg : pydub.AudioSegment
        A `pydub` segment decoded from `wav_bytes`. Its timestamps and duration
        exactly match the converted WAV.

    Raises
    ------
    AudioConversionError
        If `ffmpeg` is not installed, the input cannot be decoded, or conversion
        produces no output.

    Notes
    -----
    - This function standardizes the audio format to keep timestamps consistent
      across VAD, tone muting, normalizing, and transcription.
    - The conversion is **lossless** when the source is already PCM WAV at
      16 kHz mono s16; otherwise FFmpeg performs high-quality resampling/
      downmixing.
    - This is a whole-file (non-streaming) helper; for very large files consider
      a chunked/streaming approach.
    """
    if not isinstance(input_bytes, (bytes, bytearray, memoryview)) or len(input_bytes) == 0:
        raise AudioConversionError("No audio data provided.")

    if shutil.which("ffmpeg") is None:
        raise AudioConversionError("ffmpeg not found in PATH.")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", "pipe:0",          # stdin input
        "-vn", "-sn", "-dn",     # ignore video/subs/data streams
        "-map", "a:0",           # select first audio stream explicitly
        "-acodec", "pcm_s16le",  # 16-bit PCM
        "-ar", "16000",          # 16 kHz sample rate
        "-ac", "1",              # mono
        "-f", "wav",             # WAV container
        "pipe:1"                 # stdout
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=bytes(input_bytes),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", "replace")
        raise AudioConversionError(f"FFmpeg conversion failed:\n{err}") from e

    wav_bytes = proc.stdout
    if not wav_bytes:
        raise AudioConversionError("ffmpeg produced no output.")

    # Parse the standardized WAV (no external ffmpeg call needed here).
    seg = AudioSegment.from_wav(io.BytesIO(wav_bytes))
    return wav_bytes, seg


def validate_audio(
        file: FileStorage,
        *,
        min_duration_s: float = 2.0,
        return_wav_bytes: bool = False,
) -> Union[Tuple[float, AudioSegment], Tuple[float, AudioSegment, bytes]]:
    """
    Validate an uploaded audio file and standardize it to **16 kHz / mono / s16 WAV**.

    The function ensures the upload is present, decodable, and meets a minimum
    duration. It converts the audio to a consistent format to keep timestamps
    aligned across the pipeline (VAD → tone muting → normalization → transcription).

    Parameters
    ----------
    file
        The Werkzeug `FileStorage` object from `request.files['audio']`.
    min_duration_s
        Minimum acceptable duration (seconds). Uploads shorter than this raise
        `AudioValidationError`.
    return_wav_bytes
        If True, include the converted WAV bytes in the return tuple for direct
        persistence or immediate upload to a transcription API.

    Returns
    -------
    duration_s : float
        Duration (in seconds) of the standardized `AudioSegment`.
    segment : pydub.AudioSegment
        The audio decoded from the converted WAV (16 kHz, mono, 16-bit PCM).
    wav_bytes : bytes, optional
        Present only when `return_wav_bytes=True`. The standardized WAV
        container matching `segment`.

    Raises
    ------
    AudioValidationError
        If the upload is empty, unreadable, fails conversion/decoding, or
        is shorter than `min_duration_s`.

    Side Effects
    ------------
    Attempts to rewind the underlying file object after reading, so upstream
    code can re-read if required. Some stream-like objects may not support
    seeking; failures are silently ignored.

    Notes
    -----
    - FFmpeg must be available on PATH.
    - Standardizing here ensures downstream components (e.g., WebRTC VAD and
      Whisper) operate on the same timebase and channel layout.
    """
    # Size guard (some streams may not support SEEK_END)
    try:
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
    except Exception:
        size = None

    if size == 0:
        raise AudioValidationError("Uploaded file is empty.")

    # Read all bytes (non-streaming pipeline)
    raw = file.read()
    try:
        file.seek(0)  # best effort rewind
    except Exception:
        pass

    if not raw:
        raise AudioValidationError("Uploaded file is empty or unreadable.")

    # Convert to standardized WAV and decode to AudioSegment
    try:
        wav_bytes, seg = bytes_to_16k_mono_s16_wav(raw)
    except AudioConversionError as e:
        # Normalize lower-level errors for the route layer
        raise AudioValidationError(str(e)) from e

    # Duration check
    dur = float(seg.duration_seconds or 0.0)
    if dur < float(min_duration_s):
        raise AudioValidationError(f"Audio file is too short: {dur:.2f} seconds.")

    return (dur, seg, wav_bytes) if return_wav_bytes else (dur, seg)


def audiosegment_to_wav_bytes(seg: AudioSegment) -> bytes:
    """Export a pydub segment to WAV bytes in-memory."""
    buf = io.BytesIO()
    seg.export(buf, format="wav")
    return buf.getvalue()

########################################################################
#                      FFMPEG NORMALIZATION                            #
#                                                                      #
#                                                                      #
# ------------------------------ Config -------------------------------#

@dataclass(frozen=True)
class LoudnormParams:
    target_lufs: float = -16.0     # good for speech/podcasts & Whisper
    true_peak_dbtp: float = -1.5   # keep a little headroom to avoid intersample clipping
    lra: float = 7.0               # keep dynamics somewhat tight for intelligibility
    dual_mono: bool = False        # set True if stereo contains duplicated mono speech
    # post-output format controls (None => preserve original)
    output_sample_rate: Optional[int] = None
    output_channels: Optional[int] = None  # e.g., 1 to fold to mono for Whisper


# --------------------------- Format helpers -------------------------

_FMT_BY_WIDTH = {
    1: "u8",
    2: "s16le",
    3: "s24le",
    4: "s32le",
}


def _ffmpeg_check() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH. Please install FFmpeg.")


def _seg_raw_fmt(seg: AudioSegment) -> str:
    """Return the ffmpeg sample format for this segment's sample width."""
    try:
        return _FMT_BY_WIDTH[seg.sample_width]
    except KeyError:
        raise ValueError(f"Unsupported sample_width={seg.sample_width} bytes")


def _as_pcm_bytes(seg: AudioSegment) -> bytes:
    """Raw PCM bytes of the AudioSegment (little-endian)."""
    return seg.raw_data


def _parse_loudnorm_json(stderr_text: str) -> Dict[str, str]:
    """
    FFmpeg prints the pass-1 loudnorm stats as JSON to stderr.
    We take the *last* JSON block from stderr (handles filters/log chatter).
    """
    start = stderr_text.rfind("{")
    end = stderr_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        # Sometimes `print_format=summary` or localized logs interfere;
        # try a regex fallback to be resilient.
        matches = re.findall(r"\{(?:.|\n)*\}", stderr_text)
        if not matches:
            raise RuntimeError("Could not parse loudnorm JSON from FFmpeg output.")
        payload = matches[-1]
    else:
        payload = stderr_text[start:end + 1]

    data = json.loads(payload)
    # FFmpeg typically gives strings; we keep them as strings to pass back to filter.
    required = ["input_i", "input_tp", "input_lra", "input_thresh", "target_offset"]
    for k in required:
        if k not in data:
            raise RuntimeError(f"Missing '{k}' in loudnorm stats JSON.")
    return data


def _run_ffmpeg(args, input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
    """Run FFmpeg and return CompletedProcess (stderr often holds stats)."""
    proc = subprocess.run(
        ["ffmpeg", *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 and not (
            # When writing to -f null -, FFmpeg can still exit 0 or non-zero depending on build.
            # We'll only strictly error on second pass (where we expect valid audio output).
            "-f" in args and "null" in args
    ):
        raise RuntimeError(
            f"ffmpeg failed (rc={proc.returncode})\nSTDERR:\n{proc.stderr.decode('utf-8', 'ignore')}"
        )
    return proc


# ---------------------- Two-pass loudness core ----------------------

def _measure_loudness(
        seg: AudioSegment,
        params: LoudnormParams,
) -> Dict[str, str]:
    """First pass: measure input EBU R128 stats using loudnorm with print_format=json."""
    fmt = _seg_raw_fmt(seg)
    args = [
        "-hide_banner", "-nostats",
        "-f", fmt,
        "-ac", str(seg.channels),
        "-ar", str(seg.frame_rate),
        "-i", "-",  # stdin
        "-af",
        (
            "loudnorm="
            f"I={params.target_lufs}:"
            f"TP={params.true_peak_dbtp}:"
            f"LRA={params.lra}:"
            f"dual_mono={'true' if params.dual_mono else 'false'}:"
            "print_format=json"
        ),
        "-f", "null", "-"  # no audio output; just stats to stderr
    ]
    proc = _run_ffmpeg(args, input_bytes=_as_pcm_bytes(seg))
    return _parse_loudnorm_json(proc.stderr.decode("utf-8", "ignore"))


def _apply_loudness(
        seg: AudioSegment,
        params,
        stats: dict,
) -> AudioSegment:
    """
    Second pass: apply FFmpeg loudnorm with measured stats; return normalized segment.

    This version is strictly length-preserving and more log-quiet:
    - Adds `-loglevel error` to suppress non-fatal chatter.
    - Verifies output duration and trims/pads by at most one sample if needed.
    - Does **not** change sample rate or channels unless `params.output_*` is set.

    Parameters
    ----------
    seg
        Input AudioSegment (already standardized earlier in the pipeline).
    params
        LoudnormParams instance (same as your existing dataclass).
    stats
        JSON dict from `_measure_loudness` (FFmpeg loudnorm pass-1).

    Returns
    -------
    AudioSegment
        Loudness-normalized audio. Same duration as `seg` within one sample.

    Raises
    ------
    RuntimeError
        If FFmpeg fails or JSON is missing a required key (handled upstream).
    """
    fmt = _seg_raw_fmt(seg)

    # Build second-pass filter with measured values (unchanged logic)
    filt = (
        "loudnorm="
        f"I={params.target_lufs}:"
        f"TP={params.true_peak_dbtp}:"
        f"LRA={params.lra}:"
        f"measured_I={stats['input_i']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:"
        f"linear=true:"
        f"dual_mono={'true' if params.dual_mono else 'false'}:"
        "print_format=summary"
    )

    # Emit WAV to stdout and load back into pydub in-memory
    args = [
        "-hide_banner", "-nostats", "-loglevel", "error",
        "-f", fmt,
        "-ac", str(seg.channels),
        "-ar", str(seg.frame_rate),
        "-i", "-",           # raw PCM in
        "-af", filt,
        "-f", "wav", "-"     # WAV out to stdout
    ]
    proc = _run_ffmpeg(args, input_bytes=_as_pcm_bytes(seg))
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg second pass failed (rc={proc.returncode})\nSTDERR:\n{proc.stderr.decode('utf-8', 'ignore')}"
        )

    out = io.BytesIO(proc.stdout)
    normalized = AudioSegment.from_file(out, format="wav")

    # --- Length sanity: keep duration within one sample of the input ---
    # pydub durations are in ms; 1 sample at frame_rate equals 1000/frame_rate ms.
    one_sample_ms = 1000.0 / max(1, seg.frame_rate)
    diff_ms = len(normalized) - len(seg)

    if abs(diff_ms) > one_sample_ms:
        if diff_ms > 0:
            # Too long: hard-trim to input length (in ms)
            normalized = normalized[:len(seg)]
        else:
            # Too short: pad with matching silence to input length
            pad_ms = len(seg) - len(normalized)
            pad = AudioSegment.silent(duration=pad_ms, frame_rate=seg.frame_rate)
            pad = pad.set_sample_width(seg.sample_width).set_channels(seg.channels)
            normalized = normalized + pad

    # Optional final format shaping for Whisper (mono 16k, etc.)
    if getattr(params, "output_sample_rate", None):
        normalized = normalized.set_frame_rate(params.output_sample_rate)
    if getattr(params, "output_channels", None):
        normalized = normalized.set_channels(params.output_channels)

    return normalized


# -------------------------- Public API ------------------------------

def normalize_audio_loudnorm(
        seg: AudioSegment,
        params: LoudnormParams | None = None,
        *,
        fallback_peak_dbfs: float = -1.0,
) -> AudioSegment:
    """
    Normalize a pydub.AudioSegment with true two-pass EBU R128 loudnorm via FFmpeg.

    - Defaults target to -16 LUFS, -1.5 dBTP true peak, LRA 7 (speech-friendly).
    - Everything stays in RAM; no temp files.
    - Returns a new AudioSegment.

    If FFmpeg isn't available or loudnorm fails, falls back to simple peak
    normalization to the specified peak dBFS (default -1.0 dBFS).

    Example:
        from pydub import AudioSegment
        audio = AudioSegment.from_file("clip.wav")
        norm = normalize_audio_loudnorm(
            audio,
            LoudnormParams(output_sample_rate=16000, output_channels=1)
        )
    """
    if params is None:
        params = LoudnormParams()

    try:
        _ffmpeg_check()
        stats = _measure_loudness(seg, params)
        return _apply_loudness(seg, params, stats)
    except Exception as e:
        # Robust fallback: peak normalize using pydub only
        # Bring the loudest peak to `fallback_peak_dbfs` (e.g., -1.0 dBFS).
        try:
            peak_adjust = fallback_peak_dbfs - seg.max_dBFS
            out = seg.apply_gain(peak_adjust)
            if params.output_sample_rate:
                out = out.set_frame_rate(params.output_sample_rate)
            if params.output_channels:
                out = out.set_channels(params.output_channels)
            return out
        except Exception:
            # If even fallback fails, re-raise the original error for visibility.
            raise e


########################################################################
#                      Tone Removal                                    #
#                                                                      #
#                                                                      #
# =========================== Config ==================================#

@dataclass(frozen=True)
class ToneMuteOpts:
    pad_ms: int = 50          # pad on both sides of every detected tone window
    min_ms: int = 5           # ignore micro-spikes shorter than this
    clamp_to_len: bool = True # clamp intervals to audio length (recommended)

# =========================== Public API ==============================#
def mute_detected_tones(
        seg,                       # pydub.AudioSegment
        tone_json: Dict[str, Any], # {"tone_detect": {...}} as you provided
        opts: ToneMuteOpts = ToneMuteOpts(),
):
    """
    Return a *new* AudioSegment with detected tone windows muted (silenced).
    Efficient: edits raw PCM bytes in-place (on a copy) using frame-accurate indexing.
    """
    if not tone_json:
        return seg

    det = tone_json.get("tone_detect") or tone_json  # allow passing just the inner object
    intervals_ms = _collect_intervals_ms(det, pad_ms=opts.pad_ms, min_ms=opts.min_ms)
    if not intervals_ms:
        return seg

    intervals_ms = _merge_intervals(intervals_ms)
    return _mute_intervals_bytes(seg, intervals_ms, clamp_to_len=opts.clamp_to_len)


# ========================= Interval helpers =========================

def _collect_intervals_ms(detect: Dict[str, Any], pad_ms: int, min_ms: int) -> List[Tuple[int, int]]:
    """
    Collect (start_ms, end_ms) from all supported tone categories.
    Pads each window by pad_ms on both ends. Filters very short blips (<min_ms).
    """
    out: List[Tuple[int, int]] = []

    def add(start_s: Any, end_s: Any):
        try:
            s = float(start_s)
            e = float(end_s)
        except Exception:
            return
        if not (e > s):
            return
        s_ms = max(0, int(round((s * 1000) - pad_ms)))
        e_ms = max(0, int(round((e * 1000) + pad_ms)))
        if e_ms - s_ms >= max(1, int(min_ms)):
            out.append((s_ms, e_ms))

    # Known categories (use whatever appears; ignore unknowns)
    for key in ("pulsed_tone", "two_tone", "long_tone", "hi_low_tone", "mdc_tone", "dtmf_tone"):
        items = detect.get(key) or []
        for it in items:
            # Every item should have start/end (sometimes strings)
            add(it.get("start"), it.get("end"))

    return out


def _merge_intervals(intervals: Iterable[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge overlapping/adjacent intervals in ms."""
    s = sorted(intervals, key=lambda x: x[0])
    if not s:
        return []
    merged: List[Tuple[int, int]] = []
    cur_s, cur_e = s[0]
    for a, b in s[1:]:
        if a <= cur_e:  # overlap or touching
            if b > cur_e:
                cur_e = b
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = a, b
    merged.append((cur_s, cur_e))
    return merged


# ========================= Byte-edit muting =========================

def _mute_intervals_bytes(seg: AudioSegment,
                          intervals_ms: list[tuple[int, int]],
                          clamp_to_len: bool = True) -> AudioSegment:
    """
    Zero out (mute) raw PCM frames for the given millisecond intervals, in-place on a copy.

    Improvements vs. your previous version:
    - Uses **floor** for start and **ceil** for end when mapping ms→frames,
      preventing 1-frame erosion of tone windows.
    - Correctly uses 0x80 for 8-bit unsigned PCM silence; 0x00 for signed PCM.
    - Handles partial trailing fragments consistently.

    Parameters
    ----------
    seg
        Source AudioSegment (any PCM width, any channel count).
    intervals_ms
        List of (start_ms, end_ms) pairs. Each pair is treated as
        [start, end) in time; end is **exclusive** after ceil() mapping.
    clamp_to_len
        If True, clamps indices to the audio length to avoid IndexErrors.

    Returns
    -------
    AudioSegment
        New segment with all specified intervals muted. Sample rate, sample width,
        channel count, and total duration are preserved.
    """
    frame_rate   = seg.frame_rate
    channels     = seg.channels
    sample_width = seg.sample_width              # bytes per sample (1, 2, 3, 4)
    frame_width  = seg.frame_width               # bytes per frame (sample_width * channels)
    raw          = seg.raw_data
    n_frames     = len(raw) // frame_width

    # Precompute silence patterns
    if sample_width == 1:
        # 8-bit PCM is unsigned; midline 128 is "silence"
        silence_sample = bytes((128,))
        tail_silence_byte = b"\x80"
    else:
        silence_sample = b"\x00" * sample_width
        tail_silence_byte = b"\x00"
    silence_frame = silence_sample * channels

    data = bytearray(raw)

    # ms -> frame index helpers with floor/ceil
    def ms_to_frame_idx_start(ms: int) -> int:
        idx = int(math.floor(ms * frame_rate / 1000.0))
        if clamp_to_len:
            idx = max(0, min(n_frames, idx))
        return idx

    def ms_to_frame_idx_end(ms: int) -> int:
        idx = int(math.ceil(ms * frame_rate / 1000.0))
        if clamp_to_len:
            idx = max(0, min(n_frames, idx))
        return idx

    for s_ms, e_ms in intervals_ms:
        start_idx = ms_to_frame_idx_start(s_ms)
        end_idx   = ms_to_frame_idx_end(e_ms)
        if end_idx <= start_idx:
            continue

        start_b = start_idx * frame_width
        end_b   = end_idx   * frame_width
        bytes_span = end_b - start_b

        # Whole frames
        frames_to_mute = bytes_span // frame_width
        if frames_to_mute:
            data[start_b : start_b + frames_to_mute * frame_width] = silence_frame * frames_to_mute

        # Partial trailing fragment (should be rare, but handle safely)
        tail = bytes_span - frames_to_mute * frame_width
        if tail > 0:
            off = start_b + frames_to_mute * frame_width
            data[off : off + tail] = tail_silence_byte * tail

    return seg._spawn(bytes(data))

def collect_tone_intervals_ms(
        tone_json: Dict[str, Any],
        opts: ToneMuteOpts = ToneMuteOpts(),
) -> List[Tuple[int, int]]:
    """
    Public helper: return merged/clamped (start_ms,end_ms) tone windows
    using the same rules as mute_detected_tones().
    """
    if not tone_json:
        return []
    det = tone_json.get("tone_detect") or tone_json
    intervals = _collect_intervals_ms(det, pad_ms=opts.pad_ms, min_ms=opts.min_ms)
    if not intervals:
        return []
    return _merge_intervals(intervals)


def reduce_detected_tones_for_transcribe(
        seg: AudioSegment,
        tone_json: Dict[str, Any],
        *,
        replacement_ms: int = 0,
        opts: ToneMuteOpts = ToneMuteOpts(),
) -> Tuple[AudioSegment, Optional[Dict[str, Any]]]:
    """
    Build a *shorter* version of seg by removing tone windows entirely
    (replacement_ms=0) or compressing each merged tone-window to a fixed
    silence (replacement_ms>0).

    Returns:
      (new_seg, time_map)

    time_map can be used to remap Whisper timestamps from new_seg timebase
    back to the original seg timebase.
    """
    intervals = collect_tone_intervals_ms(tone_json, opts=opts)
    if not intervals:
        return seg, None

    total_ms = len(seg)
    # Clamp to [0,total_ms]
    clamped: List[Tuple[int, int]] = []
    for s, e in intervals:
        s2 = max(0, min(total_ms, int(s)))
        e2 = max(0, min(total_ms, int(e)))
        if e2 > s2:
            clamped.append((s2, e2))
    if not clamped:
        return seg, None
    clamped = _merge_intervals(clamped)

    # Build output + mapping entries
    out = AudioSegment.empty()
    map_entries: List[Dict[str, Any]] = []

    def _append_keep(orig_s_ms: int, orig_e_ms: int):
        nonlocal out
        if orig_e_ms <= orig_s_ms:
            return
        chunk = seg[orig_s_ms:orig_e_ms]
        new_s_ms = len(out)
        out += chunk
        new_e_ms = len(out)
        map_entries.append({
            "kind": "keep",
            "orig_start_s": orig_s_ms / 1000.0,
            "orig_end_s": orig_e_ms / 1000.0,
            "new_start_s": new_s_ms / 1000.0,
            "new_end_s": new_e_ms / 1000.0,
        })

    def _append_insert(orig_anchor_ms: int, dur_ms: int):
        nonlocal out
        if dur_ms <= 0:
            return
        new_s_ms = len(out)
        silence = AudioSegment.silent(duration=int(dur_ms), frame_rate=seg.frame_rate)
        silence = silence.set_sample_width(seg.sample_width).set_channels(seg.channels)
        out += silence
        new_e_ms = len(out)
        # Insert spans map to a single anchor point in original time (no duration)
        map_entries.append({
            "kind": "insert",
            "orig_start_s": orig_anchor_ms / 1000.0,
            "orig_end_s": orig_anchor_ms / 1000.0,
            "new_start_s": new_s_ms / 1000.0,
            "new_end_s": new_e_ms / 1000.0,
        })

    pos = 0
    removed_total_ms = 0
    inserted_total_ms = 0

    for s_ms, e_ms in clamped:
        if s_ms > pos:
            _append_keep(pos, s_ms)
        removed_total_ms += (e_ms - s_ms)

        if int(replacement_ms) > 0:
            _append_insert(s_ms, int(replacement_ms))
            inserted_total_ms += int(replacement_ms)

        pos = e_ms

    if pos < total_ms:
        _append_keep(pos, total_ms)

    time_map = {
        "version": 1,
        "orig_duration_s": total_ms / 1000.0,
        "new_duration_s": len(out) / 1000.0,
        "removed_total_s": removed_total_ms / 1000.0,
        "inserted_total_s": inserted_total_ms / 1000.0,
        "cut_total_s": (removed_total_ms - inserted_total_ms) / 1000.0,
        "entries": map_entries,  # ordered, contiguous in new timeline
    }
    return out, time_map


def remap_whisper_timestamps_to_original(
        resp: Dict[str, Any],
        time_map: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Mutates and returns resp: remaps segment/word timestamps from the shortened
    (new) timeline back to the original (orig) timeline using time_map.
    """
    if not isinstance(resp, dict) or not time_map:
        return resp

    entries = list(time_map.get("entries") or [])
    if not entries:
        return resp

    new_ends = [float(e.get("new_end_s") or 0.0) for e in entries]

    def map_time(t: Any) -> float:
        try:
            tt = float(t)
        except Exception:
            return 0.0
        if tt <= 0.0:
            return 0.0

        i = bisect.bisect_left(new_ends, tt)
        if i >= len(entries):
            # clamp to end of original audio
            return float(time_map.get("orig_duration_s") or entries[-1].get("orig_end_s") or 0.0)

        ent = entries[i]
        ns = float(ent.get("new_start_s") or 0.0)
        ne = float(ent.get("new_end_s") or ns)
        os = float(ent.get("orig_start_s") or 0.0)
        oe = float(ent.get("orig_end_s") or os)

        # Defensive clamp within entry
        if tt < ns:
            tt = ns
        if tt > ne:
            tt = ne

        if ent.get("kind") == "insert":
            return os  # anchor point

        # keep span: linear map
        # (lengths match by construction)
        return os + (tt - ns)

    # remap top-level duration to original, preserve transcribed duration for debugging
    if "duration" in resp:
        resp["duration_transcribed"] = resp.get("duration")
        resp["duration"] = float(time_map.get("orig_duration_s") or resp.get("duration") or 0.0)
    else:
        resp["duration"] = float(time_map.get("orig_duration_s") or 0.0)
        resp["duration_transcribed"] = float(time_map.get("new_duration_s") or 0.0)

    segs = resp.get("segments") or []
    if isinstance(segs, list):
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            s0 = map_time(seg.get("start", 0.0))
            e0 = map_time(seg.get("end", s0))
            if e0 < s0:
                e0 = s0
            seg["start"] = s0
            seg["end"] = e0

            words = seg.get("words")
            if isinstance(words, list):
                for w in words:
                    if not isinstance(w, dict):
                        continue
                    ws = map_time(w.get("start", 0.0))
                    we = map_time(w.get("end", ws))
                    if we < ws:
                        we = ws
                    w["start"] = ws
                    w["end"] = we

    return resp
