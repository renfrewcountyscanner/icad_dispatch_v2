# ────────────────────────── helpers ──────────────────────────
import ipaddress
import os
import re
import uuid
from urllib.parse import urlparse

from werkzeug.exceptions import BadRequest


def _norm_str(x):
    return None if x is None else str(x).strip()


def _parse_to_float(val):
    """Return float(val) or None if it can’t be coerced."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_to_int(val):
    """Return int(val) or None if it can’t be coerced."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_int_or_list(value: str | None) -> int | list[int] | None:
    """
    Convert a comma‑separated query‑string value to ``int`` or ``list[int]``.

    Returns ``None`` if *value* is ``None``.
    """
    if value is None:
        return None
    parts = value.split(",")
    try:
        ints = [int(p.strip()) for p in parts if p.strip()]
    except ValueError as exc:
        raise BadRequest(f"Invalid integer in query parameter: {value}") from exc
    return ints[0] if len(ints) == 1 else ints


def _parse_str_or_list(value: str | None) -> str | list[str] | None:
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return parts[0] if len(parts) == 1 else parts


def _generate_api_key() -> str:
    """
    Return a random 128-bit UUID in the canonical
    8-4-4-4-12 format, e.g.
        2a0f333e-6ce9-45de-bbd3-e37df69a264b
    """
    return str(uuid.uuid4())


def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "t", "yes", "on")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except Exception:
        return False


def choose_cookie_domain() -> str | None:
    # Prefer explicit env if it's a real domain
    dom = (os.getenv("SESSION_COOKIE_DOMAIN") or "").strip()
    if dom:
        if dom.lower() == "localhost" or _is_ip(dom) or _is_ip_based_hostname(dom):
            return None  # host-only for localhost/IP/IP-based names
        return dom  # FQDN allowed
    # Else try BASE_URL host
    host = (urlparse(os.getenv("BASE_URL", "")).hostname or "").strip()
    if not host or host.lower() == "localhost" or _is_ip(host) or _is_ip_based_hostname(host):
        return None
    return host


def _is_ip_based_hostname(host: str) -> bool:
    """Return True if the host ends with 4 numeric octets like .192.168.90.130."""
    return bool(re.search(r'\.\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', host))


alert_test_payload = {
    'radio_system_id': 3,
    'talkgroup': '200',
    'talkgroup_name': "Fire Dispatch 1",
    'duration_s': 48.14575,
    'start_epoch_s': 1766437181,
    'audio_url': 'https://audio.bcfirewire.com/detect/2025/12/22/3_200_1766437181.mp3',
    'address_extracted': {
        'raw_text': '2733 Doty Hill Road, Ridgebury Township, PA',
        'street': '2733 Doty Hill Rd',
        'city': 'Ridgebury Township',
        'county': 'Bradford',
        'state': 'PA',
        'country': 'US',
        'confidence': 1.0,
        'extra': {}
    },
    'address_geocoded': {
        'lat': 41.997721,
        'lng': -76.738714,
        'formatted_address': '2733 Doty Hill Rd, Gillett, PA 16925, USA',
        'county': 'Bradford County', 'state': 'PA',
        'city': 'Gillett',
        'postal_code': '16925',
        'country': 'US',
        'maps_url': 'https://www.google.com/maps/search/?api=1&query=41.997721,-76.738714'
    },
    'incident_category': 'Fire',
    'incident_type': 'Brush/Woods Fire',
    'tone_detect': {
        "pulsed": [],
        "two_tone": [
            {
                "tone_id": "qc_1",
                "detected": [
                    1820.0,
                    564.3
                ],
                "tone_a_length": 1.0,
                "tone_b_length": 3.05,
                "start": 0.914,
                "end": 4.964
            },
            {
                "tone_id": "qc_2",
                "detected": [
                    726.7,
                    1123.0
                ],
                "tone_a_length": 1.05,
                "tone_b_length": 3.05,
                "start": 6.764,
                "end": 10.864
            },
            {
                "tone_id": "qc_3",
                "detected": [
                    1820.0,
                    564.3
                ],
                "tone_a_length": 1.0,
                "tone_b_length": 3.05,
                "start": 23.464,
                "end": 27.514
            },
            {
                "tone_id": "qc_4",
                "detected": [
                    726.7,
                    1123.0
                ],
                "tone_a_length": 1.0,
                "tone_b_length": 3.05,
                "start": 29.364,
                "end": 33.414
            }
        ],
        "long_tone": [],
        "hi_low": [],
        "mdc": [],
        "dtmf": []
    }

}

alert_test_transcribe = {
    'duration': 48.14575,
    'language': 'en',
    'model': 'large-v3',
    'segments': [
        {
            'avg_logprob': -0.43474263741689567,
            'compression_ratio': 1.1,
            'end': 18.3,
            'id': 0,
            'no_speech_prob': 0.1942138671875,
            'start': 13.399999999999999,
            'temperature': 0.0,
            'text': ' Ridgebury MS, third and final page, greater valley for the assist, Ridgebury Township',
            'words': [
                {
                    'end': 13.96,
                    'probability': 0.513671875,
                    'start': 13.399999999999999,
                    'word': ' Ridgebury'
                },
                {
                    'end': 14.28,
                    'probability': 0.395751953125,
                    'start': 13.96,
                    'word': ' MS,'
                },
                {
                    'end': 14.66,
                    'probability': 0.26611328125,
                    'start': 14.48,
                    'word': ' third'
                },
                {
                    'end': 14.78,
                    'probability': 0.98388671875,
                    'start': 14.66,
                    'word': ' and'
                },
                {
                    'end': 14.96,
                    'probability': 0.9970703125,
                    'start': 14.78,
                    'word': ' final'
                },
                {
                    'end': 15.32,
                    'probability': 0.927734375,
                    'start': 14.96,
                    'word': ' page,'
                },
                {
                    'end': 15.84,
                    'probability': 0.3388671875,
                    'start': 15.6,
                    'word': ' greater'
                },
                {
                    'end': 16.12,
                    'probability': 0.9697265625,
                    'start': 15.84,
                    'word': ' valley'
                },
                {
                    'end': 16.4,
                    'probability': 0.939453125,
                    'start': 16.12,
                    'word': ' for'
                },
                {
                    'end': 16.5,
                    'probability': 0.958984375,
                    'start': 16.4,
                    'word': ' the'
                },
                {
                    'end': 16.84,
                    'probability': 0.95166015625,
                    'start': 16.5,
                    'word': ' assist,'
                },
                {
                    'end': 17.72,
                    'probability': 0.977783203125,
                    'start': 17.18,
                    'word': ' Ridgebury'
                },
                {
                    'end': 18.3,
                    'probability': 0.871337890625,
                    'start': 17.72,
                    'word': ' Township'
                }
            ]
        },
        {
            'avg_logprob': -0.43474263741689567,
            'compression_ratio': 1.1,
            'end': 21.06,
            'id': 1,
            'no_speech_prob': 0.1942138671875,
            'start': 18.3,
            'temperature': 0.0,
            'text': ' 2733 Doty Hill Road for brush fire.',
            'words': [{'end': 19.24, 'probability': 0.757568359375, 'start': 18.3, 'word': ' 2733'},
                      {'end': 19.88, 'probability': 0.703125, 'start': 19.24, 'word': ' Doty'},
                      {'end': 20.08, 'probability': 0.96875, 'start': 19.88, 'word': ' Hill'},
                      {'end': 20.38, 'probability': 0.85205078125, 'start': 20.08, 'word': ' Road'},
                      {'end': 20.58, 'probability': 0.93017578125, 'start': 20.38, 'word': ' for'},
                      {'end': 20.86, 'probability': 0.83056640625, 'start': 20.58, 'word': ' brush'},
                      {'end': 21.06, 'probability': 0.671875, 'start': 20.86, 'word': ' fire.'}]},
        {
            'avg_logprob': -0.411458330684238,
            'compression_ratio': 1.1732283464566928,
            'end': 42.18,
            'id': 2,
            'no_speech_prob': 0.2181396484375,
            'start': 36.339999999999996,
            'temperature': 0.0,
            'text': ' Bridge Ferry EMS, third of final page, agreed to rally, second due for the assist, Bridge',
            'words': [{'end': 36.76, 'probability': 0.3125, 'start': 36.339999999999996, 'word': ' Bridge'},
                      {'end': 37.06, 'probability': 0.7191162109375, 'start': 36.76, 'word': ' Ferry'},
                      {'end': 37.38, 'probability': 0.833984375, 'start': 37.06, 'word': ' EMS,'},
                      {'end': 39.5, 'probability': 0.435302734375, 'start': 37.68, 'word': ' third'},
                      {'end': 39.66, 'probability': 0.445068359375, 'start': 39.5, 'word': ' of'},
                      {'end': 39.86, 'probability': 0.91552734375, 'start': 39.66, 'word': ' final'},
                      {'end': 40.16, 'probability': 0.97705078125, 'start': 39.86, 'word': ' page,'},
                      {'end': 40.46, 'probability': 0.41650390625, 'start': 40.3, 'word': ' agreed'},
                      {'end': 40.58, 'probability': 0.9814453125, 'start': 40.46, 'word': ' to'},
                      {'end': 40.72, 'probability': 0.97998046875, 'start': 40.58, 'word': ' rally,'},
                      {'end': 41.04, 'probability': 0.96875, 'start': 40.84, 'word': ' second'},
                      {'end': 41.36, 'probability': 0.642578125, 'start': 41.04, 'word': ' due'},
                      {'end': 41.52, 'probability': 0.95751953125, 'start': 41.36, 'word': ' for'},
                      {'end': 41.62, 'probability': 0.96923828125, 'start': 41.52, 'word': ' the'},
                      {'end': 41.82, 'probability': 0.9619140625, 'start': 41.62, 'word': ' assist,'},
                      {'end': 42.18, 'probability': 0.91357421875, 'start': 42.04, 'word': ' Bridge'}]},
        {
            'avg_logprob': -0.411458330684238,
            'compression_ratio': 1.1732283464566928,
            'end': 46.22,
            'id': 3,
            'no_speech_prob': 0.2181396484375, 'start': 42.18, 'temperature': 0.0,
            'text': ' Ferry Township, 2733 Doty Hill Road for a brush fire, 1600.',
            'words': [
                {'end': 42.38, 'probability': 0.99560546875, 'start': 42.18, 'word': ' Ferry'},
                {'end': 42.86, 'probability': 0.9765625, 'start': 42.38, 'word': ' Township,'},
                {'end': 43.74, 'probability': 0.960693359375, 'start': 42.96, 'word': ' 2733'},
                {'end': 44.42, 'probability': 0.736572265625, 'start': 43.74, 'word': ' Doty'},
                {'end': 44.6, 'probability': 0.99169921875, 'start': 44.42, 'word': ' Hill'},
                {'end': 44.92, 'probability': 0.89697265625, 'start': 44.6, 'word': ' Road'},
                {'end': 45.14, 'probability': 0.685546875, 'start': 44.92, 'word': ' for'},
                {'end': 45.22, 'probability': 0.8115234375, 'start': 45.14, 'word': ' a'},
                {'end': 45.44, 'probability': 0.9658203125, 'start': 45.22, 'word': ' brush'},
                {'end': 45.72, 'probability': 0.9150390625, 'start': 45.44, 'word': ' fire,'},
                {'end': 46.22, 'probability': 0.822265625, 'start': 45.86, 'word': ' 1600.'}
            ]
        }
    ],
    'task': 'transcribe',
    'text': 'Ridgebury MS, third and final page, greater valley for the assist, Ridgebury Township 2733 Doty Hill Road for brush fire. Bridge Ferry EMS, third of final page, agreed to rally, second due for the assist, Bridge Ferry Township, 2733 Doty Hill Road for a brush fire, 1600.',
    'words': [
        {'end': 13.96, 'probability': 0.513671875, 'start': 13.399999999999999, 'word': ' Ridgebury'},
        {'end': 14.28, 'probability': 0.395751953125, 'start': 13.96, 'word': ' MS,'},
        {'end': 14.66, 'probability': 0.26611328125, 'start': 14.48, 'word': ' third'},
        {'end': 14.78, 'probability': 0.98388671875, 'start': 14.66, 'word': ' and'},
        {'end': 14.96, 'probability': 0.9970703125, 'start': 14.78, 'word': ' final'},
        {'end': 15.32, 'probability': 0.927734375, 'start': 14.96, 'word': ' page,'},
        {'end': 15.84, 'probability': 0.3388671875, 'start': 15.6, 'word': ' greater'},
        {'end': 16.12, 'probability': 0.9697265625, 'start': 15.84, 'word': ' valley'},
        {'end': 16.4, 'probability': 0.939453125, 'start': 16.12, 'word': ' for'},
        {'end': 16.5, 'probability': 0.958984375, 'start': 16.4, 'word': ' the'},
        {'end': 16.84, 'probability': 0.95166015625, 'start': 16.5, 'word': ' assist,'},
        {'end': 17.72, 'probability': 0.977783203125, 'start': 17.18, 'word': ' Ridgebury'},
        {'end': 18.3, 'probability': 0.871337890625, 'start': 17.72, 'word': ' Township'},
        {'end': 19.24, 'probability': 0.757568359375, 'start': 18.3, 'word': ' 2733'},
        {'end': 19.88, 'probability': 0.703125, 'start': 19.24, 'word': ' Doty'},
        {'end': 20.08, 'probability': 0.96875, 'start': 19.88, 'word': ' Hill'},
        {'end': 20.38, 'probability': 0.85205078125, 'start': 20.08, 'word': ' Road'},
        {'end': 20.58, 'probability': 0.93017578125, 'start': 20.38, 'word': ' for'},
        {'end': 20.86, 'probability': 0.83056640625, 'start': 20.58, 'word': ' brush'},
        {'end': 21.06, 'probability': 0.671875, 'start': 20.86, 'word': ' fire.'},
        {'end': 36.76, 'probability': 0.3125, 'start': 36.339999999999996, 'word': ' Bridge'},
        {'end': 37.06, 'probability': 0.7191162109375, 'start': 36.76, 'word': ' Ferry'},
        {'end': 37.38, 'probability': 0.833984375, 'start': 37.06, 'word': ' EMS,'},
        {'end': 39.5, 'probability': 0.435302734375, 'start': 37.68, 'word': ' third'},
        {'end': 39.66, 'probability': 0.445068359375, 'start': 39.5, 'word': ' of'},
        {'end': 39.86, 'probability': 0.91552734375, 'start': 39.66, 'word': ' final'},
        {'end': 40.16, 'probability': 0.97705078125, 'start': 39.86, 'word': ' page,'},
        {'end': 40.46, 'probability': 0.41650390625, 'start': 40.3, 'word': ' agreed'},
        {'end': 40.58, 'probability': 0.9814453125, 'start': 40.46, 'word': ' to'},
        {'end': 40.72, 'probability': 0.97998046875, 'start': 40.58, 'word': ' rally,'},
        {'end': 41.04, 'probability': 0.96875, 'start': 40.84, 'word': ' second'},
        {'end': 41.36, 'probability': 0.642578125, 'start': 41.04, 'word': ' due'},
        {'end': 41.52, 'probability': 0.95751953125, 'start': 41.36, 'word': ' for'},
        {'end': 41.62, 'probability': 0.96923828125, 'start': 41.52, 'word': ' the'},
        {'end': 41.82, 'probability': 0.9619140625, 'start': 41.62, 'word': ' assist,'},
        {'end': 42.18, 'probability': 0.91357421875, 'start': 42.04, 'word': ' Bridge'},
        {'end': 42.38, 'probability': 0.99560546875, 'start': 42.18, 'word': ' Ferry'},
        {'end': 42.86, 'probability': 0.9765625, 'start': 42.38, 'word': ' Township,'},
        {'end': 43.74, 'probability': 0.960693359375, 'start': 42.96, 'word': ' 2733'},
        {'end': 44.42, 'probability': 0.736572265625, 'start': 43.74, 'word': ' Doty'},
        {'end': 44.6, 'probability': 0.99169921875, 'start': 44.42, 'word': ' Hill'},
        {'end': 44.92, 'probability': 0.89697265625, 'start': 44.6, 'word': ' Road'},
        {'end': 45.14, 'probability': 0.685546875, 'start': 44.92, 'word': ' for'},
        {'end': 45.22, 'probability': 0.8115234375, 'start': 45.14, 'word': ' a'},
        {'end': 45.44, 'probability': 0.9658203125, 'start': 45.22, 'word': ' brush'},
        {'end': 45.72, 'probability': 0.9150390625, 'start': 45.44, 'word': ' fire,'},
        {'end': 46.22, 'probability': 0.822265625, 'start': 45.86, 'word': ' 1600.'}
    ]
}

alert_test_fired_trigger = [
    {
        'alert_trigger_id': 76,
        'radio_system_id': 3,
        'alert_trigger_name': 'Greater Valley EMS',
        'alert_trigger_type': 'AND',
        'alert_trigger_tone_tolerance': 2,
        'alert_trigger_ignore_time': 300,
        'alert_trigger_talkgroup': None,
        'alert_trigger_stream_url': None,
        'alert_trigger_enable_discord': 1,
        'alert_trigger_enable_make': 1,
        'alert_trigger_enable_telegram': 1,
        'alert_trigger_enabled': 1,
        'alert_trigger_enable_n8n': 1,
        'two_tone_sets': [
            {
                'two_tone_set_id': 72,
                'alert_trigger_id': 76,
                'rule_uid': 'c4138fd519b7d749',
                'freq_a_hz': 726.6,
                'min_len_a_s': 0.5,
                'freq_b_hz': 1125.0,
                'min_len_b_s': 2.5,
                'tol_pct': None,
                'sort_order': 0
            }
        ],
        'long_tone_sets': [],
        'hi_low_sets': [],
        'pulsed_sets': [],
        'dtmf_sequences': [],
        'enable_pushover': 0,
        'pushover_group_token': None,
        'pushover_app_token': None,
        'pushover_body': None,
        'pushover_subject': None,
        'pushover_sound': None,
        'last_fired_at': 1766436879
    }
]
