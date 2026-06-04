# lib/vad_hybrid.py
from __future__ import annotations
from typing import List, Tuple, Dict, Any, Optional
from pydub.audio_segment import AudioSegment

# expects a global webrtcvad.Vad() instance named _webrtc_vad (same as your code)
# e.g. somewhere in your app startup:
import webrtcvad
_webrtc_vad = webrtcvad.Vad(2)  # 0..3 (aggressiveness)

Interval   = Tuple[float, float]
LabeledSeg = Tuple[str, float, float]  # ("voice"|"silence", start, end)

# ---------- tiny interval utils ----------
def _merge(ivl: List[Interval], *, gap: float = 0.0, min_len: float = 0.0) -> List[Interval]:
    if not ivl: return []
    ivl = sorted(ivl)
    out = [ivl[0]]
    for s, e in ivl[1:]:
        ps, pe = out[-1]
        if s - pe <= gap:
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    if min_len > 0:
        out = [(s, e) for (s, e) in out if (e - s) >= min_len]
    return out

def _subtract(A: List[Interval], B: List[Interval]) -> List[Interval]:
    if not A: return []
    if not B: return _merge(A)
    B = _merge(B)
    out: List[Interval] = []
    for (as_, ae) in sorted(A):
        curr = [(as_, ae)]
        for (bs, be) in B:
            nxt: List[Interval] = []
            for (cs, ce) in curr:
                if be <= cs or bs >= ce:
                    nxt.append((cs, ce)); continue
                if bs > cs: nxt.append((cs, bs))
                if be < ce: nxt.append((be, ce))
            curr = [(s, e) for (s, e) in nxt if e > s]
        out.extend(curr)
    return _merge(out)

def _union(A: List[Interval], B: List[Interval], *, gap: float = 0.0) -> List[Interval]:
    return _merge(sorted(A) + sorted(B), gap=gap)

def _label_from_voice(voice: List[Interval], total: float) -> List[LabeledSeg]:
    voice = _merge(voice)
    out: List[LabeledSeg] = []
    t = 0.0
    for s, e in voice:
        if s > t: out.append(("silence", t, s))
        out.append(("voice", s, e))
        t = e
    if t < total: out.append(("silence", t, total))
    return out

# ---------- tone & transcript windows ----------
def _tone_windows(detect_result: Any, guard_ms: int) -> List[Interval]:
    def _be(t: Dict[str, Any]) -> Optional[Interval]:
        s = t.get("start_s", t.get("payload", {}).get("start"))
        e = t.get("end_s",   t.get("payload", {}).get("end"))
        if s is None or e is None: return None
        return float(s), float(e)
    keys = ("two_tone_result","long_result","hi_low_result","pulsed_result","dtmf_result","mdc_result")
    all_items: List[Dict[str, Any]] = []
    for k in keys:
        it = getattr(detect_result, k, None)
        if it: all_items.extend(it)
    g = guard_ms / 1000.0
    iv = []
    for t in all_items:
        be = _be(t)
        if be:
            s, e = be
            iv.append((max(0.0, s - g), e + g))
    return _merge(iv)

def _word_windows(transcribe_response: Optional[Dict[str, Any]],
                  *, max_gap_s: float, guard_ms: int, min_len_s: float) -> List[Interval]:
    if not transcribe_response: return []
    words = []
    if "words" in transcribe_response:
        words = transcribe_response["words"] or []
    else:
        for seg in (transcribe_response.get("segments") or []):
            words.extend(seg.get("words") or [])
    words = [w for w in words if "start" in w and "end" in w]
    if not words: return []
    words.sort(key=lambda w: (w["start"], w["end"]))
    bunches: List[list] = []
    cur: List[dict] = []
    for w in words:
        if not cur: cur=[w]; continue
        gap = float(w["start"]) - float(cur[-1]["end"])
        if gap <= max_gap_s: cur.append(w)
        else: bunches.append(cur); cur=[w]
    if cur: bunches.append(cur)
    g = guard_ms / 1000.0
    iv = []
    for bunch in bunches:
        s = float(bunch[0]["start"]) - g
        e = float(bunch[-1]["end"]) + g
        if (e - s) >= min_len_s:
            iv.append((max(0.0, s), e))
    return _merge(iv, gap=0.1, min_len=min_len_s)

# ---------- core: direct WebRTC VAD + tone/transcript reconciliation ----------
def vad_segments_webrtc_tone_tx(
        seg: AudioSegment,
        *,
        detect_result: Any = None,
        transcribe_response: Optional[Dict[str, Any]] = None,
        frame_ms: int = 20,                 # WebRTC allowed: 10/20/30
        smooth_window_frames: int = 5,      # majority window, must be odd
        enter_threshold_frames: int = 2,    # hysteresis to ENTER speech
        exit_threshold_frames: int = 3,     # hysteresis to EXIT speech
        pad_ms: int = 120,                  # hangover around speech
        tone_guard_ms: int = 80,            # widen tone masks
        word_guard_ms: int = 120,           # widen word windows
        max_word_gap_s: float = 0.60,       # join nearby words
        merge_gap_s: float = 0.10,          # merge same-kind neighbors
        min_speech_s: float = 0.25,         # drop tiny speech blips
        min_silence_s: float = 0.20,        # drop tiny silence blips
) -> List[LabeledSeg]:
    """
    Direct WebRTC VAD → tone masking → transcript union → tidy.
    Returns: [("voice"|"silence", start_s, end_s), ...]
    """
    if len(seg) == 0:
        return []

    # prepare PCM for WebRTC
    seg = seg.set_frame_rate(16_000).set_channels(1).set_sample_width(2)
    pcm = seg.raw_data
    sr = 16_000
    bytes_per_sample = 2
    samples_per_frame = int(sr * frame_ms / 1000)
    bytes_per_frame = samples_per_frame * bytes_per_sample
    n_frames = len(pcm) // bytes_per_frame
    if n_frames == 0:
        return [("silence", 0.0, len(seg)/1000.0)]

    # 1) raw WebRTC decisions
    frames = []
    for i in range(n_frames):
        start_s = i * (frame_ms / 1000.0)
        end_s   = start_s + (frame_ms / 1000.0)
        chunk   = pcm[i*bytes_per_frame:(i+1)*bytes_per_frame]
        is_speech = _webrtc_vad.is_speech(chunk, sr)   # <-- direct call
        frames.append((start_s, end_s, is_speech))

    # 2) smoothing: rolling majority
    if smooth_window_frames % 2 == 0:
        smooth_window_frames += 1
    half = smooth_window_frames // 2
    smoothed = []
    for i in range(n_frames):
        lo = max(0, i - half)
        hi = min(n_frames, i + half + 1)
        votes = sum(1 for _,_,v in frames[lo:hi] if v)
        smoothed.append(votes >= ((hi - lo)//2 + 1))

    # 3) hysteresis + hangover → base voice intervals
    pad_frames = max(0, int(round(pad_ms / frame_ms)))
    in_speech = False
    c_s = c_ns = 0
    seg_start_idx = None
    base_voice: List[Interval] = []

    for i, v in enumerate(smoothed):
        if v:
            c_s += 1; c_ns = 0
        else:
            c_ns += 1; c_s = 0

        if not in_speech and c_s >= enter_threshold_frames:
            in_speech = True
            seg_start_idx = max(0, i - pad_frames)

        if in_speech and c_ns >= exit_threshold_frames:
            end_idx = min(n_frames - 1, i - 1 + pad_frames)
            s = frames[seg_start_idx][0]
            e = frames[end_idx][1]
            if e > s: base_voice.append((s, e))
            in_speech = False
            seg_start_idx = None

    if in_speech and seg_start_idx is not None:
        s = frames[seg_start_idx][0]
        e = frames[-1][1]
        if e > s: base_voice.append((s, e))

    base_voice = _merge(base_voice, gap=merge_gap_s, min_len=min_speech_s)

    # 4) tone masking (force silence)
    tone_iv = _tone_windows(detect_result, guard_ms=tone_guard_ms) if detect_result else []
    voice_minus_tones = _subtract(base_voice, tone_iv)

    # 5) transcript union (force speech outside tones)
    word_iv = _word_windows(transcribe_response,
                            max_gap_s=max_word_gap_s,
                            guard_ms=word_guard_ms,
                            min_len_s=min_speech_s)
    word_no_tones = _subtract(word_iv, tone_iv)
    voice_union = _union(voice_minus_tones, word_no_tones, gap=merge_gap_s)

    # 6) label and cleanup
    total = len(seg) / 1000.0
    labeled = _label_from_voice(voice_union, total)

    # drop tiny blips and re-merge
    pruned: List[LabeledSeg] = []
    for k,s,e in labeled:
        if (e - s) >= (min_speech_s if k == "voice" else min_silence_s):
            pruned.append((k,s,e))
    if not pruned:
        return [("silence", 0.0, total)]

    out: List[LabeledSeg] = [pruned[0]]
    for k,s,e in pruned[1:]:
        pk, ps, pe = out[-1]
        if k == pk and (s - pe) <= merge_gap_s:
            out[-1] = (pk, ps, e)
        else:
            out.append((k,s,e))
    return out
