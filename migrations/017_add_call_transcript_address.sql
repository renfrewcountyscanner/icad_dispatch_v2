BEGIN;

-- Add extracted address JSON (normalized address from LLM)
ALTER TABLE call_transcripts
    ADD COLUMN address_extracted_json TEXT;

-- Add geocoded address JSON (lat/lng + components from Maps)
ALTER TABLE call_transcripts
    ADD COLUMN address_geocoded_json  TEXT;

COMMIT;
