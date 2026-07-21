from lib.operations_status import get_operations_status


class FakeDB:
    def __init__(self):
        self.calls = 0
        self.sql = []

    def execute_query(self, sql, _params, fetch_mode):
        self.calls += 1
        self.sql.append(sql)
        if self.calls == 1:
            return {"success": True, "result": {"calls_received": 4, "calls_transcribed": 3, "addresses_extracted": 2, "addresses_geocoded": 1, "geocode_pending": 2, "corrections_applied": 1, "latest_call_epoch": 100}}
        return {"success": True, "result": [{"call_id": 9, "start_epoch_s": 90, "system_name": "Dispatch", "talkgroup_name": "Fire", "text_full": "Example transcript"}]}


def test_operations_status_returns_metrics_and_retry_candidates():
    db = FakeDB()
    status = get_operations_status(db, 24)
    assert status["success"] is True
    assert status["result"]["metrics"]["calls_received"] == 4
    assert status["result"]["retry_candidates"][0]["call_id"] == 9
    assert "chr(37)" in db.sql[2]
