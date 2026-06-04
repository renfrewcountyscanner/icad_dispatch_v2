-- 023_radio_system_tone_extra_knobs.sql
PRAGMA foreign_keys = ON;
BEGIN;

-- Front-end SNR gate
ALTER TABLE radio_system_tone_settings
    ADD COLUMN fe_snr_above_noise_db REAL DEFAULT 1.0
        CHECK (fe_snr_above_noise_db BETWEEN 0.0 AND 100.0);

UPDATE radio_system_tone_settings
SET fe_snr_above_noise_db = 1.0
WHERE fe_snr_above_noise_db IS NULL;

-- Prevent pairing A/B that are too close together (Hz)
ALTER TABLE radio_system_tone_settings
    ADD COLUMN two_tone_min_pair_separation_hz INTEGER DEFAULT 10
        CHECK (two_tone_min_pair_separation_hz BETWEEN 0 AND 50000);

UPDATE radio_system_tone_settings
SET two_tone_min_pair_separation_hz = 10
WHERE two_tone_min_pair_separation_hz IS NULL;

-- Max gap between DTMF presses (seconds)
ALTER TABLE radio_system_tone_settings
    ADD COLUMN dtmf_sequence_gap_s REAL DEFAULT 0.3
        CHECK (dtmf_sequence_gap_s BETWEEN 0.0 AND 10.0);

UPDATE radio_system_tone_settings
SET dtmf_sequence_gap_s = 0.3
WHERE dtmf_sequence_gap_s IS NULL;

COMMIT;
