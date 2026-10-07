"""SQLite 连接管理与结构初始化。

设计要点：
- 条款使用“稳定编号”(stable_no)：创建时由编辑者指定，删除后编号作废、不再复用；
- 条款每次生效修改写入 clause_revisions，草案版本(head_version)单调递增；
- 提案(proposals)承载 替换/删除/意见 三种来源；并行同条款冲突保持 pending 待决；
- 发布生成 snapshots / snapshot_clauses 不可变快照，与后续草稿隔离。
"""

import os
import sqlite3
import threading

DB_PATH = os.environ.get("CLAUSE_DB_PATH", "/data/clauses.db")

_lock = threading.Lock()
_conn = None

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS drafts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    author       TEXT NOT NULL,
    head_version INTEGER NOT NULL DEFAULT 1,   -- 当前草稿版本号，随每次生效修订 +1
    published_at TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS clauses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id    INTEGER NOT NULL REFERENCES drafts(id),
    stable_no   TEXT NOT NULL,                 -- 稳定编号，删除后作废、不复用
    body        TEXT NOT NULL,                 -- 最近一次生效正文
    is_active   INTEGER NOT NULL DEFAULT 1,    -- 0 = 已删除（墓碑，保留编号与原意见）
    current_rev INTEGER NOT NULL DEFAULT 1,    -- 该条款最近一次生效修订号
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(draft_id, stable_no)
);

CREATE TABLE IF NOT EXISTS clause_revisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    clause_id     INTEGER NOT NULL REFERENCES clauses(id),
    draft_id      INTEGER NOT NULL REFERENCES drafts(id),
    rev           INTEGER NOT NULL,            -- 条款自身修订序号（1 为创建）
    draft_version INTEGER NOT NULL,            -- 生效时的草案版本
    kind          TEXT NOT NULL,               -- replace / delete
    body          TEXT NOT NULL,               -- 该修订后的正文（删除时保留删除前正文）
    source_proposal_id INTEGER REFERENCES proposals(id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(clause_id, rev)
);

-- 替换 / 删除 / 意见 的统一来源载体
CREATE TABLE IF NOT EXISTS proposals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id         INTEGER NOT NULL REFERENCES drafts(id),
    kind             TEXT NOT NULL,            -- replace / delete / comment
    clause_id        INTEGER REFERENCES clauses(id),
    stable_no        TEXT NOT NULL,            -- 冗余留存：编号删除后仍可追溯原引用
    body             TEXT,                     -- 替换正文（意见为 NULL）
    note             TEXT,                     -- 意见 / 修改说明
    author           TEXT NOT NULL,
    baseline_version INTEGER NOT NULL,         -- 编辑者所见版本（乐观并发基线）
    status           TEXT NOT NULL DEFAULT 'pending', -- pending/applied/closed
    parent_id        INTEGER REFERENCES proposals(id), -- 待决结案提案指向原冲突提案
    resolution       TEXT,                     -- keep/take/custom
    resolved_by      TEXT,
    applied_clause_rev INTEGER,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    applied_at       TEXT,
    resolved_at      TEXT
);

-- 意见附着于稳定编号；条款删除后标记失效，绝不转挂邻条
CREATE TABLE IF NOT EXISTS comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id     INTEGER NOT NULL REFERENCES drafts(id),
    clause_id    INTEGER NOT NULL REFERENCES clauses(id),
    stable_no    TEXT NOT NULL,                 -- 原引用编号（持久保留）
    proposal_id  INTEGER REFERENCES proposals(id),
    author       TEXT NOT NULL,
    content      TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1,    -- 条款删除后置 0，引用编号不变
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id         INTEGER NOT NULL REFERENCES drafts(id),
    version          INTEGER NOT NULL,           -- 发布时的草案版本
    published_by     TEXT NOT NULL,
    title            TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(draft_id, version)
);

-- 不可变快照内容：只复制当时生效条款
CREATE TABLE IF NOT EXISTS snapshot_clauses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    stable_no   TEXT NOT NULL,
    body        TEXT NOT NULL,
    rev         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clauses_draft     ON clauses(draft_id);
CREATE INDEX IF NOT EXISTS idx_revisions_draft   ON clause_revisions(draft_id, draft_version);
CREATE INDEX IF NOT EXISTS idx_proposals_draft   ON proposals(draft_id, draft_id, status);
CREATE INDEX IF NOT EXISTS idx_comments_clause   ON comments(clause_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_snapshot ON snapshot_clauses(snapshot_id);
"""


def get_conn():
    """返回进程内共享连接；写操作由 service 层锁串行化。"""
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                directory = os.path.dirname(os.path.abspath(DB_PATH))
                os.makedirs(directory, exist_ok=True)
                conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
                _conn = conn
    return _conn


def init_db():
    """幂等建表，容器启动即完成数据初始化。"""
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
