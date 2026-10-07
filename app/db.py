"""SQLite 数据访问层：连接管理与建表。"""
import os
import sqlite3

DB_PATH = os.environ.get(
    "APP_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "app.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 每次被采纳的修订生成一个不可变版本（完整快照），number 在草案内递增
CREATE TABLE IF NOT EXISTS versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id    INTEGER NOT NULL REFERENCES drafts(id),
    number      INTEGER NOT NULL,
    author      TEXT NOT NULL,
    message     TEXT NOT NULL,
    source      TEXT NOT NULL,           -- 修订来源: initial / proposal:<id> / resolution:<id>
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (draft_id, number)
);

CREATE TABLE IF NOT EXISTS clause_states (
    version_id    INTEGER NOT NULL REFERENCES versions(id),
    clause_number TEXT NOT NULL,
    text          TEXT NOT NULL,
    PRIMARY KEY (version_id, clause_number)
);

-- 编辑者基于所见版本提交的修订提案；同条款冲突时进入 pending 待决
CREATE TABLE IF NOT EXISTS proposals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id       INTEGER NOT NULL REFERENCES drafts(id),
    base_version   INTEGER NOT NULL,     -- 提交时所见的版本号
    author         TEXT NOT NULL,
    action         TEXT NOT NULL,        -- replace | delete
    clause_number  TEXT NOT NULL,
    new_text       TEXT,
    status         TEXT NOT NULL,        -- merged | pending | resolved-ours | resolved-theirs | resolved-merged
    result_version INTEGER,              -- 生效后产生的版本号
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at    TEXT
);

-- 意见附着于稳定条款编号；条款被删除后保留原编号并标记 invalid
CREATE TABLE IF NOT EXISTS comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id      INTEGER NOT NULL REFERENCES drafts(id),
    clause_number TEXT NOT NULL,
    base_version  INTEGER NOT NULL,
    author        TEXT NOT NULL,
    text          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | invalid
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 发布产生的不可变快照
CREATE TABLE IF NOT EXISTS publications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id   INTEGER NOT NULL REFERENCES drafts(id),
    version    INTEGER NOT NULL,
    author     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS publication_clauses (
    publication_id INTEGER NOT NULL REFERENCES publications(id),
    clause_number  TEXT NOT NULL,
    text           TEXT NOT NULL,
    PRIMARY KEY (publication_id, clause_number)
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
