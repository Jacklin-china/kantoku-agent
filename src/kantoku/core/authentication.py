"""Single-owner local accounts and persistent sessions.

Existing application data remains available in legacy mode until the first
account is explicitly registered. No database migration occurs on reads.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import re
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from kantoku.config.runtime_paths import resolve_runtime_path
from kantoku.core.database import database_manager

SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
LOCK_SECONDS = 15 * 60
MAX_ATTEMPTS = 5


class AuthenticationError(ValueError):
    """A safe error message for authentication failures."""


def _password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)


def _validate_credentials(username: str, password: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]{3,32}", username):
        raise AuthenticationError("用户名须为 3–32 位中文、字母、数字、下划线或短横线")
    if not 8 <= len(password) <= 128 or len(password.encode()) > 512:
        raise AuthenticationError("密码须为 8–128 个字符")


class LocalAuthentication:
    """A single owner account and revocable, hashed random session tokens."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = resolve_runtime_path(path)
        self._clock = clock

    @contextmanager
    def _connect(self) -> Iterator:
        with closing(database_manager.connect(self.path)) as connection:
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _has_schema(connection) -> bool:
        return bool(connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_owner'"
        ).fetchone())

    def registered(self) -> bool:
        if not self.path.exists():
            return False
        with self._connect() as connection:
            return self._has_schema(connection) and bool(connection.execute(
                "SELECT 1 FROM auth_owner WHERE id=1"
            ).fetchone())

    def register(self, username: str, password: str) -> str:
        _validate_credentials(username, password)
        salt = secrets.token_bytes(16)
        digest = _password_digest(password, salt)
        token = secrets.token_urlsafe(32)
        now = int(self._clock())
        # Back up the current live database before adding authentication tables.
        # No existing business rows are moved or deleted.
        if self.path.exists():
            with self._connect() as connection:
                exists = self._has_schema(connection)
            if not exists:
                database_manager.backup(self.path, owner="authentication.migration")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_owner (id INTEGER PRIMARY KEY CHECK(id=1), "
                "username TEXT NOT NULL, salt BLOB NOT NULL, password_digest BLOB NOT NULL, "
                "failed_attempts INTEGER NOT NULL DEFAULT 0, "
                "locked_until INTEGER NOT NULL DEFAULT 0)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_sessions (digest TEXT PRIMARY KEY, "
                "created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, "
                "revoked INTEGER NOT NULL DEFAULT 0)"
            )
            if connection.execute("SELECT 1 FROM auth_owner WHERE id=1").fetchone():
                raise AuthenticationError("此设备已注册账号")
            connection.execute(
                "INSERT INTO auth_owner(id,username,salt,password_digest) VALUES (1,?,?,?)",
                (username, salt, digest),
            )
            connection.execute(
                "INSERT INTO auth_sessions(digest,created_at,expires_at) VALUES (?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), now, now + SESSION_TTL_SECONDS),
            )
        return token

    def login(self, username: str, password: str) -> str:
        if len(username) > 32 or len(password) > 128 or len(password.encode()) > 512:
            raise AuthenticationError("用户名或密码不正确")
        now = int(self._clock())
        token = secrets.token_urlsafe(32)
        accepted = False
        locked = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._has_schema(connection):
                raise AuthenticationError("请先注册账号")
            row = connection.execute(
                "SELECT username,salt,password_digest,failed_attempts,locked_until "
                "FROM auth_owner WHERE id=1"
            ).fetchone()
            if row is None:
                raise AuthenticationError("请先注册账号")
            if row[4] > now:
                locked = True
            else:
                matches = hmac.compare_digest(_password_digest(password, row[1]), row[2])
                matches = matches and hmac.compare_digest(
                    username.encode(), row[0].encode()
                )
                if matches:
                    connection.execute(
                        "UPDATE auth_owner SET failed_attempts=0, locked_until=0 WHERE id=1"
                    )
                    connection.execute(
                        "INSERT INTO auth_sessions(digest,created_at,expires_at) VALUES (?,?,?)",
                        (hashlib.sha256(token.encode()).hexdigest(), now,
                         now + SESSION_TTL_SECONDS),
                    )
                    accepted = True
                else:
                    attempts = row[3] + 1 if row[4] == 0 else 1
                    locked_until = now + LOCK_SECONDS if attempts >= MAX_ATTEMPTS else 0
                    connection.execute(
                        "UPDATE auth_owner SET failed_attempts=?,locked_until=? WHERE id=1",
                        (attempts, locked_until),
                    )
        if locked:
            raise AuthenticationError("尝试次数过多，请 15 分钟后重试")
        if not accepted:
            raise AuthenticationError("用户名或密码不正确")
        return token

    def valid_session(self, token: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token) or not self.path.exists():
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as connection:
            if not self._has_schema(connection):
                return False
            row = connection.execute(
                "SELECT expires_at,revoked FROM auth_sessions WHERE digest=?", (digest,)
            ).fetchone()
        return bool(row and row[0] > int(self._clock()) and not row[1])

    def revoke_session(self, token: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as connection:
            if self._has_schema(connection):
                connection.execute("UPDATE auth_sessions SET revoked=1 WHERE digest=?", (digest,))

    def session_started_at(self, token: str) -> int | None:
        """当前会话的创建时间（epoch 秒）。只读查询，令牌无效时返回 None。"""
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as connection:
            if not self._has_schema(connection):
                return None
            row = connection.execute(
                "SELECT created_at FROM auth_sessions WHERE digest=?", (digest,)
            ).fetchone()
        return int(row[0]) if row else None

    def profile(self) -> dict:
        with self._connect() as connection:
            if not self._has_schema(connection):
                raise AuthenticationError("请先登录")
            owner = connection.execute("SELECT username FROM auth_owner WHERE id=1").fetchone()
            if not owner:
                raise AuthenticationError("请先登录")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_profile'"
            ).fetchone()
            row = connection.execute(
                "SELECT nickname, avatar, avatar_type FROM auth_profile WHERE id=1"
            ).fetchone() if exists else None
            avatar = (f"data:{row[2]};base64,{base64.b64encode(row[1]).decode()}"
                      if row and row[1] else None)
            return {"username": owner[0], "nickname": row[0] if row else owner[0],
                    "avatar": avatar}

    def update_profile(
        self, nickname: str, avatar: str | None, remove_avatar: bool = False
    ) -> dict:
        """更新资料。

        ``avatar=None`` 沿用「保持原头像」的历史语义；清除头像必须显式传
        ``remove_avatar=True``，否则旧调用方会误删用户头像。
        """
        nickname = nickname.strip()
        if not nickname or len(nickname) > 32:
            raise AuthenticationError("昵称须为 1–32 个字符")
        if remove_avatar and avatar is not None:
            raise AuthenticationError("不能同时更换与移除头像")
        content = None
        media_type = None
        if avatar is not None:
            if not isinstance(avatar, str) or len(avatar) > 700000:
                raise AuthenticationError("头像文件过大")
            try:
                header, encoded = avatar.split(",", 1)
                if header not in {"data:image/png;base64", "data:image/jpeg;base64"}:
                    raise AuthenticationError("头像只支持 PNG 或 JPEG")
                content = base64.b64decode(encoded, validate=True)
                if not content or len(content) > 500000:
                    raise AuthenticationError("头像大小不能超过 500 KB")
                with Image.open(io.BytesIO(content)) as picture:
                    if (picture.format not in {"PNG", "JPEG"}
                            or picture.width * picture.height > 4000000):
                        raise AuthenticationError("头像格式或尺寸不支持")
                    media_type = "image/png" if picture.format == "PNG" else "image/jpeg"
                    if header != f"data:{media_type};base64":
                        raise AuthenticationError("头像格式不匹配")
                    picture.verify()
            except (ValueError, binascii.Error, UnidentifiedImageError, OSError) as error:
                raise AuthenticationError("头像文件无效") from error
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_profile'"
            ).fetchone()
        if not exists:
            database_manager.backup(self.path, owner="authentication.profile-migration")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._has_schema(connection) or not connection.execute(
                "SELECT 1 FROM auth_owner WHERE id=1"
            ).fetchone():
                raise AuthenticationError("请先登录")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_profile "
                "(id INTEGER PRIMARY KEY CHECK(id=1), nickname TEXT NOT NULL, "
                "avatar BLOB, avatar_type TEXT)"
            )
            if remove_avatar:
                connection.execute(
                    "INSERT INTO auth_profile(id,nickname,avatar,avatar_type) "
                    "VALUES (1,?,NULL,NULL) "
                    "ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname, "
                    "avatar=NULL, avatar_type=NULL",
                    (nickname,),
                )
            else:
                connection.execute(
                    "INSERT INTO auth_profile(id,nickname,avatar,avatar_type) VALUES (1,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname, "
                    "avatar=COALESCE(excluded.avatar,auth_profile.avatar), "
                    "avatar_type=COALESCE(excluded.avatar_type,auth_profile.avatar_type)",
                    (nickname, content, media_type),
                )
        return self.profile()

    def change_password(self, old_password: str, new_password: str, token: str) -> None:
        if len(old_password) > 128 or len(old_password.encode()) > 512:
            raise AuthenticationError("原密码不正确")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._has_schema(connection):
                raise AuthenticationError("请先登录")
            row = connection.execute(
                "SELECT username,salt,password_digest FROM auth_owner WHERE id=1"
            ).fetchone()
            if not row or not hmac.compare_digest(_password_digest(old_password, row[1]), row[2]):
                raise AuthenticationError("原密码不正确")
            _validate_credentials(row[0], new_password)
            salt = secrets.token_bytes(16)
            connection.execute(
                "UPDATE auth_owner SET salt=?,password_digest=?,failed_attempts=0,"
                "locked_until=0 WHERE id=1",
                (salt, _password_digest(new_password, salt)),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked=1 WHERE digest != ?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )
