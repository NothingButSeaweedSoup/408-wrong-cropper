"""SQLite 访问层。

刻意不用 SQLAlchemy：单文件库 + 十几条 SQL，标准库 sqlite3 足够，
省掉一个重依赖和一层 ORM 心智负担。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    year        INTEGER NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    pdf_path    TEXT    NOT NULL,
    page_count  INTEGER NOT NULL DEFAULT 0,
    -- uploaded -> rendering -> ocr -> split -> corrected | failed
    status      TEXT    NOT NULL DEFAULT 'uploaded',
    progress    INTEGER NOT NULL DEFAULT 0,
    message     TEXT    NOT NULL DEFAULT '',
    dpi         REAL    NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page_no     INTEGER NOT NULL,
    image_path  TEXT    NOT NULL,
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    UNIQUE (paper_id, page_no)
);

CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    question_no INTEGER NOT NULL,
    subject     TEXT    NOT NULL DEFAULT 'ds',
    type        TEXT    NOT NULL DEFAULT 'choice',
    score       REAL,
    -- 小问指向所属大题；大题 parent_id 为 NULL
    parent_id   INTEGER REFERENCES questions(id) ON DELETE CASCADE,
    order_no    INTEGER NOT NULL DEFAULT 0,
    -- JSON: {"blocks":[{page_no,y0,y1,path}], "sub_marks":[{page_no,y0,label}]}
    bbox_json   TEXT    NOT NULL DEFAULT '{}',
    image_paths TEXT    NOT NULL DEFAULT '[]',
    source      TEXT    NOT NULL DEFAULT 'auto',   -- auto | manual
    created_at  TEXT    NOT NULL,
    UNIQUE (paper_id, question_no, order_no)
);

CREATE INDEX IF NOT EXISTS idx_questions_paper ON questions(paper_id, order_no);

CREATE TABLE IF NOT EXISTS exports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_ids    TEXT NOT NULL DEFAULT '[]',
    question_ids TEXT NOT NULL DEFAULT '[]',
    filename     TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);

-- ---------------------------------------------------------------- 用户系统
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,     -- pbkdf2_sha256$迭代次数$salt$hash
    display_name  TEXT NOT NULL DEFAULT '',
    is_admin      INTEGER NOT NULL DEFAULT 0,   -- 管理员标记（也可由 .env 的 ZC_ADMIN_USERS 指定）
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- 得分记录：每次做一套真题一条
CREATE TABLE IF NOT EXISTS exam_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    paper_year    INTEGER NOT NULL,
    practice_date TEXT NOT NULL,              -- YYYY-MM-DD
    total_score   REAL NOT NULL,
    ds_score      REAL NOT NULL,
    co_score      REAL NOT NULL,
    os_score      REAL NOT NULL,
    cn_score      REAL NOT NULL,
    -- 录入明细：各选择题组答对数、各综合题得分与满分（含当时用的卷面结构）
    detail_json   TEXT NOT NULL DEFAULT '{}',
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exam_user ON exam_records(user_id, practice_date);

-- 全局设置（管理员可改，比如是否允许注册）
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    config.ensure_dirs()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    """老库补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "is_admin" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")


# ---------------------------------------------------------------- 小工具
def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def question_out(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """把 questions 行转成 API 返回结构（JSON 字段展开）。"""
    d = row_to_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    d["image_paths"] = load_json(d.get("image_paths"), [])
    d["bbox"] = load_json(d.get("bbox_json"), {})
    return d


# ---------------------------------------------------------------- 全局设置
def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row is not None else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def allow_register(conn: sqlite3.Connection) -> bool:
    default = "1" if config.ALLOW_REGISTER_DEFAULT else "0"
    return get_setting(conn, "allow_register", default) not in ("0", "", "false", "False")
