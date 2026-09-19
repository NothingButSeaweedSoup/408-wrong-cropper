"""用户与登录态。

刻意不引 passlib/jwt：标准库 hashlib.pbkdf2_hmac 够用，
token 直接随机串存库（单机 SQLite，省掉 JWT 的签名/续期逻辑）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

from .. import config

PBKDF2_ITERATIONS = 200_000
USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fa5]{2,24}$")  # 字母数字下划线 + 中文，2~24 位


class AuthError(ValueError):
    """参数不合法 / 认证失败，消息可直接给前端看。"""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iterations)
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def _validate(username: str, password: str) -> None:
    if not USERNAME_RE.match(username or ""):
        raise AuthError("用户名需 2~24 位，只能用中英文、数字、下划线")
    if len(password or "") < config.MIN_PASSWORD_LEN:
        raise AuthError(f"密码至少 {config.MIN_PASSWORD_LEN} 位")


def public_user(row: sqlite3.Row | dict) -> dict:
    keys = row.keys() if isinstance(row, sqlite3.Row) else row
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "is_admin": bool(row["is_admin"]) if "is_admin" in keys else False,
        "created_at": row["created_at"],
    }


def is_admin(row: sqlite3.Row | dict | None) -> bool:
    if row is None:
        return False
    keys = row.keys() if isinstance(row, sqlite3.Row) else row
    return bool(row["is_admin"]) if "is_admin" in keys else False


def set_admin(conn: sqlite3.Connection, user_id: int, flag: bool) -> None:
    conn.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if flag else 0, user_id))


def sync_admin_users(conn: sqlite3.Connection) -> dict:
    """把 .env 里 ZC_ADMIN_USERS 指定的账号置为管理员。

    返回 {"promoted": [...], "missing": [...]}：missing 是还没注册的用户名，
    等 ta 注册时 create_user 会自动给上管理员标记（不用再重启）。
    """
    promoted: list[str] = []
    missing: list[str] = []
    for name in sorted(config.ADMIN_USERS):
        row = conn.execute("SELECT id, is_admin FROM users WHERE username=?", (name,)).fetchone()
        if row is None:
            missing.append(name)
            continue
        if not row["is_admin"]:
            conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (int(row["id"]),))
            promoted.append(name)
    return {"promoted": promoted, "missing": missing}


def create_user(conn: sqlite3.Connection, username: str, password: str, display_name: str = "") -> sqlite3.Row:
    username = (username or "").strip()
    _validate(username, password)
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise AuthError("这个用户名已经被注册了")
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, display_name, is_admin, created_at) VALUES (?,?,?,?,?)",
        (
            username,
            hash_password(password),
            (display_name or "").strip()[:32],
            1 if config.is_admin_username(username) else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    return conn.execute("SELECT * FROM users WHERE id=?", (int(cur.lastrowid),)).fetchone()


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row:
    username = (username or "").strip()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None or not verify_password(password or "", row["password_hash"]):
        raise AuthError("用户名或密码不正确")
    if config.is_admin_username(username) and not is_admin(row):
        # .env 里刚加进管理员名单：登录时补上，省得等重启
        conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (int(row["id"]),))
        row = conn.execute("SELECT * FROM users WHERE id=?", (int(row["id"]),)).fetchone()
    return row


def issue_token(conn: sqlite3.Connection, user_id: int) -> dict:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires = now + timedelta(days=config.TOKEN_TTL_DAYS)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user_id, now.strftime("%Y-%m-%d %H:%M:%S"), expires.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return {"token": token, "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S")}


def user_by_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    row = conn.execute(
        """SELECT u.*, s.expires_at AS expires_at FROM sessions s
           JOIN users u ON u.id = s.user_id WHERE s.token = ?""",
        (token,),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] < datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        return None
    return row


def revoke(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def cleanup_expired(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    return cur.rowcount


def bearer_token(header_value: str | None) -> str:
    """支持 `Authorization: Bearer xxx` 和裸 token 两种写法。"""
    if not header_value:
        return ""
    value = header_value.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def user_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])


__all__ = [
    "AuthError",
    "authenticate",
    "bearer_token",
    "cleanup_expired",
    "create_user",
    "hash_password",
    "is_admin",
    "issue_token",
    "public_user",
    "revoke",
    "set_admin",
    "sync_admin_users",
    "user_by_token",
    "user_count",
    "verify_password",
]
