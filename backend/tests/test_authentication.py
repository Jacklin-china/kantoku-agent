"""Offline local-account tests use isolated databases only."""

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from PIL import Image

from kantoku.core.authentication import AuthenticationError, LocalAuthentication
from kantoku.core.database import database_manager


@pytest.fixture
def setup(tmp_path):
    now = [1700000000]
    path = tmp_path / "authentication.sqlite"

    def create():
        return LocalAuthentication(path, clock=lambda: now[0])

    return create, now, path


def test_register_login_restart_and_revoke(setup):
    create, now, _ = setup
    auth = create()
    assert not auth.registered()
    first = auth.register("用户_01", "password-for-test")
    assert auth.registered() and auth.valid_session(first)
    with pytest.raises(AuthenticationError, match="已注册"):
        auth.register("other", "another-password")
    assert create().valid_session(first)
    second = create().login("用户_01", "password-for-test")
    auth.revoke_session(first)
    assert not auth.valid_session(first)
    assert auth.valid_session(second)
    now[0] += 30 * 86400
    assert not auth.valid_session(second)


def test_incorrect_credentials_and_lockout_survive_restart(setup):
    create, now, _ = setup
    auth = create()
    auth.register("user", "password-for-test")
    for _ in range(5):
        with pytest.raises(AuthenticationError, match="用户名或密码"):
            auth.login("user", "wrong-password")
    with pytest.raises(AuthenticationError, match="15 分钟"):
        create().login("user", "password-for-test")
    now[0] += 15 * 60
    assert create().valid_session(create().login("user", "password-for-test"))


def test_registration_migrates_with_backup_and_preserves_existing_rows(setup):
    create, _, path = setup
    with closing(database_manager.connect(path)) as connection:
        connection.execute("CREATE TABLE existing_project (name TEXT)")
        connection.execute("INSERT INTO existing_project VALUES ('keep me')")
        connection.commit()
    create().register("user", "password-for-test")
    with closing(database_manager.connect(path)) as connection:
        assert connection.execute("SELECT name FROM existing_project").fetchone() == ("keep me",)
        row = connection.execute("SELECT password_digest FROM auth_owner").fetchone()
        assert b"password-for-test" not in row[0]
    assert list((path.parent / "backups").glob("*.sqlite"))


def test_concurrent_registration_only_creates_one_owner(setup):
    create, _, _ = setup
    auth = create()

    def attempt(name):
        try:
            return auth.register(name, "password-for-test")
        except AuthenticationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["user1", "user2"]))
    assert sum(bool(result) for result in results) == 1


@pytest.mark.parametrize(("name", "password"), [
    ("x", "password-for-test"), ("bad name", "password-for-test"), ("user", "short"),
])
def test_invalid_registration_never_creates_account(setup, name, password):
    create, _, _ = setup
    auth = create()
    with pytest.raises(AuthenticationError):
        auth.register(name, password)
    assert not auth.registered()


def test_profile_and_password_survive_restart_and_revoke_other_sessions(setup):
    create, _, _ = setup
    auth = create()
    token = auth.register("user1", "password-for-test")
    other = auth.login("user1", "password-for-test")
    assert auth.profile()["nickname"] == "user1"
    assert auth.update_profile("创作者", None)["nickname"] == "创作者"
    assert create().profile()["nickname"] == "创作者"
    with pytest.raises(AuthenticationError, match="原密码"):
        auth.change_password("incorrect", "new-password", token)
    assert auth.valid_session(other)
    auth.change_password("password-for-test", "new-password", token)
    assert auth.valid_session(token)
    assert not auth.valid_session(other)
    assert create().valid_session(create().login("user1", "new-password"))


def test_invalid_avatar_is_rejected_without_replacing_saved_profile(setup):
    create, _, _ = setup
    auth = create()
    auth.register("user1", "password-for-test")
    auth.update_profile("保留昵称", None)
    with pytest.raises(AuthenticationError):
        auth.update_profile("新昵称", "data:image/png;base64,AA==")
    assert auth.profile()["nickname"] == "保留昵称"


def test_valid_avatar_persists_and_nickname_edit_does_not_erase_it(setup):
    create, _, _ = setup
    auth = create()
    auth.register("user1", "password-for-test")
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, format="PNG")
    avatar = "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()
    auth.update_profile("创作者", avatar)
    create().update_profile("新昵称", None)
    assert create().profile()["avatar"] == avatar


def test_avatar_is_removed_only_by_the_explicit_flag(setup):
    create, _, _ = setup
    auth = create()
    auth.register("user1", "password-for-test")
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, format="PNG")
    avatar = "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()
    auth.update_profile("创作者", avatar)
    # 显式不删除时，头像必须保留（旧调用方依赖该语义）。
    assert create().update_profile("创作者", None, False)["avatar"] == avatar
    assert create().update_profile("创作者", None, True)["avatar"] is None
    assert create().profile()["avatar"] is None
    with pytest.raises(AuthenticationError, match="不能同时"):
        create().update_profile("创作者", avatar, True)
    assert create().profile()["avatar"] is None
