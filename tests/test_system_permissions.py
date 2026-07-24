from flask import Flask, session

from routes.api.systems import api_systems


def _client():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_systems, url_prefix="/api/systems")
    return app.test_client()


def _login(client, *, systems=None):
    with client.session_transaction() as sess:
        sess.update(
            authenticated=True,
            user_id=10,
            is_admin=False,
            user_systems=systems or {},
        )


def test_read_only_user_cannot_modify_a_system():
    client = _client()
    _login(client, systems={7: "read"})

    response = client.patch("/api/systems/7", json={"system_name": "changed"})

    assert response.status_code == 404
    assert response.get_json()["success"] is False


def test_unassigned_user_cannot_read_a_system():
    client = _client()
    _login(client)

    response = client.get("/api/systems/7")

    assert response.status_code == 404
    assert response.get_json()["success"] is False


def test_non_admin_cannot_create_a_system():
    client = _client()
    _login(client, systems={7: "write"})

    response = client.post("/api/systems", json={"system_name": "new"})

    assert response.status_code == 403
    assert response.get_json()["success"] is False
