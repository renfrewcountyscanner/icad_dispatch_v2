from lib.alert_trigger_module import upsert_two_tone_sets


class _Db:
    def __init__(self):
        self.sql = []

    def execute_query(self, *_args, **_kwargs):
        return {"success": True, "result": []}

    def execute_commit(self, sql, _params, **_kwargs):
        self.sql.append(sql)
        return {"success": True, "result": []}


def test_two_tone_upsert_qualifies_existing_columns_for_postgres():
    db = _Db()

    upsert_two_tone_sets(
        db,
        22,
        [{"rule_uid": "rule-a", "freq_a_hz": 1153.3, "freq_b_hz": 1432.7}],
    )

    sql = db.sql[0]
    assert "excluded.freq_a_hz, alert_trigger_two_tone_sets.freq_a_hz" in sql
    assert "excluded.freq_b_hz, alert_trigger_two_tone_sets.freq_b_hz" in sql


def test_two_tone_upsert_surfaces_database_write_errors():
    class FailingDb(_Db):
        def execute_commit(self, sql, _params, **_kwargs):
            self.sql.append(sql)
            return {"success": False, "message": "database write failed"}

    try:
        upsert_two_tone_sets(FailingDb(), 22, [{"rule_uid": "rule-a"}])
    except RuntimeError as error:
        assert "database write failed" in str(error)
    else:
        raise AssertionError("Expected the failed child-rule write to be raised")
