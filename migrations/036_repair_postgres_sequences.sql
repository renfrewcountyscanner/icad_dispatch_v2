-- Repair sequences after imports/restores that inserted explicit identity values.
-- Without this, the next new system/settings row can reuse an existing primary key.
DO $$
DECLARE
    item RECORD;
    sequence_name TEXT;
    highest_id BIGINT;
BEGIN
    FOR item IN
        SELECT c.table_schema, c.table_name, c.column_name,
               pg_get_serial_sequence(format('%I.%I', c.table_schema, c.table_name), c.column_name) AS sequence_name
        FROM information_schema.columns c
        WHERE c.table_schema = current_schema()
          AND c.column_default LIKE 'nextval(%'
    LOOP
        sequence_name := item.sequence_name;
        IF sequence_name IS NULL THEN
            CONTINUE;
        END IF;

        EXECUTE format('SELECT MAX(%I) FROM %I.%I', item.column_name, item.table_schema, item.table_name)
            INTO highest_id;
        PERFORM setval(sequence_name::regclass, COALESCE(highest_id, 1), highest_id IS NOT NULL);
    END LOOP;
END $$;

INSERT INTO schema_migrations (version) VALUES (36)
ON CONFLICT DO NOTHING;
