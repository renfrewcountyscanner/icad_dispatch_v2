BEGIN;

ALTER TABLE call_transcripts
    ADD COLUMN incident_category TEXT;

ALTER TABLE call_transcripts
    ADD COLUMN incident_classification_json TEXT;

-- Optional but probably useful for querying by incident type
CREATE INDEX IF NOT EXISTS idx_call_transcripts_incident_category
    ON call_transcripts(incident_category);

COMMIT;
