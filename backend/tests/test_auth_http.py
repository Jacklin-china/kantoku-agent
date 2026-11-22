"""HTTP login boundaries and restart behavior in an isolated runtime."""

import json
import threading
from http.client import HTTPConnection

from test_web_studio import app, server  # noqa: F401 - reuse existing isolated fixtures

from kantoku.shells import web_studio


def request(port, method, path, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read()
        headers = dict(response.getheaders())
        for name in (
            "Set-Cookie", "Access-Control-Allow-Credentials", "Access-Control-Allow-Origin",
        ):
            if response.getheader(name) is not None:
                headers[name] = response.getheader(name)
        return response.status, headers, content
    finally:
        connection.close()


def test_registration_login_logout_and_restart_keep_existing_data(app, server):  # noqa: F811
    studio_headers = {"X-Studio-Token": app.token}
    assert request(server, "GET", "/api/state", headers=studio_headers)[0] == 200
    created = app.create_conversation({})
    status, _, payload = request(server, "GET", "/api/auth/status")
    assert status == 200 and json.loads(payload) == {"registered": False, "authenticated": True}
    login_headers = {"Content-Type": "application/json", "X-Kantoku-Login": "1"}
    credentials = json.dumps({"username": "user1", "password": "test-password"})
    status, headers, _ = request(server, "POST", "/api/auth/register", credentials, login_headers)
    assert status == 200
    cookie = headers["Set-Cookie"].split(";")[0]
    assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    status, headers, _ = request(server, "GET", "/api/auth/status", headers={
        "Origin": "http://127.0.0.1:5173", "Cookie": cookie,
    })
    assert status == 200 and headers["Access-Control-Allow-Credentials"] == "true"
    assert headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5173"
    assert request(server, "GET", "/api/session")[0] == 401
    for path in ("/api/state", "/media/none", "/api/runs/id/events/stream?token=" + app.token):
        assert request(server, "GET", path, headers=studio_headers)[0] == 401
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert request(server, method, "/api/conversations", "{}", studio_headers)[0] == 401
    authorized = {**studio_headers, "Cookie": cookie}
    status, _, payload = request(server, "GET", "/api/conversations", headers=authorized)
    assert status == 200 and created["id"] in payload.decode()
    assert request(server, "POST", "/api/auth/register", credentials, login_headers)[0] == 400
    assert request(server, "POST", "/api/auth/login", credentials, {
        **login_headers, "Origin": "https://attacker.example",
    })[0] == 403

    restarted = web_studio.StudioApplication()
    other = web_studio.make_server(restarted)
    thread = threading.Thread(target=other.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = request(other.server_port, "GET", "/api/auth/status",
                                  headers={"Cookie": cookie})
        assert status == 200 and json.loads(body)["authenticated"] is True
        assert request(other.server_port, "GET", "/api/state", headers={
            "Cookie": cookie, "X-Studio-Token": restarted.token,
        })[0] == 200
    finally:
        other.shutdown()
        other.server_close()
        thread.join()
        restarted.runner.close()

    assert request(server, "POST", "/api/auth/logout", "{}", authorized)[0] == 200
    assert request(server, "GET", "/api/state", headers=authorized)[0] == 401
    assert request(server, "POST", "/api/auth/login", json.dumps({
        "username": "user1", "password": "wrong-password",
    }), login_headers)[0] == 400
    assert request(server, "POST", "/api/auth/login", credentials, login_headers)[0] == 200
    assert request(server, "POST", "/api/auth/login", credentials, {
        **login_headers, "Host": f"localhost:{server}", "Origin": f"http://localhost:{server}",
    })[0] == 200
