"""业务逻辑层：草案、条款修订、提案/意见、待决合并、发布快照、版本差异。

并发与冲突规则（关键）：
- 提交 替换/删除 时记录编辑者所见版本 baseline_version；
  * 若该条款自基线以来未被改动 → 直接生效；
  * 若已被改动（同条款并行改动）→ 提案保持 pending，绝不覆盖前写；
- 同一基线上针对不同条款的并行改动互不影响，各自直接生效（自动合并）；
- 待决提案由编辑者选择 keep(保留当前)/take(采用对方)/custom(填写合并文本) 结案；
- 只要存在 pending，禁止发布；
- 发布生成不可变快照，之后的修改只影响草稿，快照不变。
"""

import difflib
import re
import sqlite3
import threading
import time

from . import db

_WRITE_LOCK = threading.RLock()

KIND_REPLACE = "replace"
KIND_DELETE = "delete"
KIND_COMMENT = "comment"
VALID_KINDS = (KIND_REPLACE, KIND_DELETE, KIND_COMMENT)


class ServiceError(Exception):
    def __init__(self, message, status=400, code="bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ---------------------------------------------------------------- 工具函数

def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _require_str(data, field, *, max_len=None):
    if not isinstance(data, dict) or field not in data:
        raise ServiceError(f"缺少字段: {field}", status=422, code="missing_field")
    value = data[field]
    if not isinstance(value, str):
        raise ServiceError(f"字段 {field} 必须为字符串", status=422, code="invalid_field")
    value = value.strip()
    if not value:
        raise ServiceError(f"字段 {field} 不能为空", status=422, code="invalid_field")
    if max_len and len(value) > max_len:
        raise ServiceError(f"字段 {field} 过长（上限 {max_len} 字符）",
                           status=422, code="invalid_field")
    return value


_STABLE_NO_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _validate_stable_no(no):
    no = _require_str({"stable_no": no}, "stable_no", max_len=64)
    if not _STABLE_NO_RE.match(no):
        raise ServiceError("条款编号仅允许字母、数字、点、下划线、短横线",
                           status=422, code="invalid_stable_no")
    return no


def _get_draft(conn, draft_id):
    row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if row is None:
        raise ServiceError(f"不存在的基线/草案: id={draft_id}",
                           status=404, code="draft_not_found")
    return row


def _get_clause(conn, draft_id, stable_no):
    return conn.execute(
        "SELECT * FROM clauses WHERE draft_id=? AND stable_no=?",
        (draft_id, stable_no),
    ).fetchone()


def sort_key(stable_no):
    """自然排序：A.2 排在 A.10 前。"""
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r"(\d+)", stable_no)]


def _next_clause_rev(conn, clause_id):
    row = conn.execute(
        "SELECT COALESCE(MAX(rev),0)+1 AS r FROM clause_revisions WHERE clause_id=?",
        (clause_id,),
    ).fetchone()
    return row["r"]


def _unified_diff(old, new, from_label, to_label):
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=from_label, tofile=to_label, lineterm="",
    ))


# ---------------------------------------------------------------- 创建草案

def create_draft(data):
    title = _require_str(data, "title", max_len=200)
    author = _require_str(data, "author", max_len=100)
    clauses_in = data.get("clauses", [])
    if not isinstance(clauses_in, list) or not clauses_in:
        raise ServiceError("创建草案至少需要一条条款", status=422, code="invalid_field")

    normalized = []
    seen = set()
    for item in clauses_in:
        if not isinstance(item, dict):
            raise ServiceError("clauses 元素必须为对象", status=422, code="invalid_field")
        no = _validate_stable_no(item.get("stable_no"))
        body = _require_str(item, "body", max_len=100000)
        if no in seen:
            raise ServiceError(f"条款编号重复: {no}", status=422, code="duplicate_stable_no")
        seen.add(no)
        normalized.append((no, body))

    conn = db.get_conn()
    with _WRITE_LOCK:
        try:
            cur = conn.execute(
                "INSERT INTO drafts(title, author) VALUES(?,?)", (title, author))
        except sqlite3.Error as exc:
            raise ServiceError(f"创建草案失败: {exc}", status=500)
        draft_id = cur.lastrowid

        for no, body in normalized:
            c = conn.execute(
                "INSERT INTO clauses(draft_id, stable_no, body) VALUES(?,?,?)",
                (draft_id, no, body))
            clause_id = c.lastrowid
            conn.execute(
                """INSERT INTO clause_revisions
                   (clause_id, draft_id, rev, draft_version, kind, body, source_proposal_id)
                   VALUES(?,?,1,1,'replace',?,NULL)""",
                (clause_id, draft_id, body))
        conn.commit()
    return get_draft(draft_id)


# ---------------------------------------------------------------- 提交提案

def submit_proposal(draft_id, data):
    kind = _require_str(data, "kind")
    if kind not in VALID_KINDS:
        raise ServiceError(f"无效的修改类型: {kind}（允许 replace/delete/comment）",
                           status=422, code="invalid_kind")
    author = _require_str(data, "author", max_len=100)
    stable_no = _validate_stable_no(data.get("stable_no"))
    baseline = data.get("baseline_version")
    if not isinstance(baseline, int) or isinstance(baseline, bool):
        raise ServiceError("baseline_version 必须为整数版本号", status=422, code="invalid_field")

    note = data.get("note")
    if note is not None and not isinstance(note, str):
        raise ServiceError("note 必须为字符串", status=422, code="invalid_field")

    body = None
    if kind == KIND_REPLACE:
        body = _require_str(data, "body", max_len=100000)
    elif kind == KIND_COMMENT:
        body = _require_str(data, "content", max_len=50000)

    conn = db.get_conn()
    with _WRITE_LOCK:
        draft = _get_draft(conn, draft_id)  # 不存在基线 → 报错
        if baseline < 1 or baseline > draft["head_version"]:
            raise ServiceError(
                f"不存在的基线版本: {baseline}（当前为 v{draft['head_version']}）",
                status=422, code="invalid_baseline")

        clause = _get_clause(conn, draft_id, stable_no)
        if clause is None:
            raise ServiceError(f"无效的条款编号: {stable_no}（编号不存在或已作废）",
                               status=422, code="invalid_stable_no")

        if kind == KIND_COMMENT:
            if not clause["is_active"]:
                raise ServiceError(f"条款 {stable_no} 已删除，不能再附新意见",
                                   status=422, code="clause_deleted")
            cur = conn.execute(
                """INSERT INTO proposals(draft_id, kind, clause_id, stable_no,
                       note, author, baseline_version, status)
                   VALUES(?,?,?,?,?,?,?, 'applied')""",
                (draft_id, kind, clause["id"], stable_no, note, author, baseline))
            proposal_id = cur.lastrowid
            conn.execute(
                """INSERT INTO comments(draft_id, clause_id, stable_no,
                       proposal_id, author, content)
                   VALUES(?,?,?,?,?,?)""",
                (draft_id, clause["id"], stable_no, proposal_id, author, body))
            conn.execute("UPDATE proposals SET applied_at=? WHERE id=?",
                         (now_ts(), proposal_id))
            conn.commit()
            return {"proposal_id": proposal_id, "status": "applied", "pending": False}

        # replace / delete
        if not clause["is_active"] and kind == KIND_DELETE:
            raise ServiceError(f"条款 {stable_no} 已删除，不能重复删除（编号已作废）",
                               status=422, code="clause_deleted")
        if not clause["is_active"] and not _clause_version_after(
                conn, clause["id"], baseline):
            # 编辑者所见基线中条款即已删除 → 编号作废
            raise ServiceError(f"条款 {stable_no} 已删除，不能再修改（编号已作废）",
                               status=422, code="clause_deleted")

        # 已存在针对该条款的待决提案 → 后写不得覆盖，直接进入待决
        existing_pending = conn.execute(
            "SELECT COUNT(1) AS n FROM proposals WHERE draft_id=? AND clause_id=? "
            "AND kind IN ('replace','delete') AND status='pending'",
            (draft_id, clause["id"]),
        ).fetchone()["n"]

        # 乐观并发：自所见基线以来该条款已被改动 → 并行同条款冲突 → 待决
        changed_since_baseline = _clause_version_after(conn, clause["id"], baseline)

        cur = conn.execute(
            """INSERT INTO proposals(draft_id, kind, clause_id, stable_no, body,
                   note, author, baseline_version, status)
               VALUES(?,?,?,?,?,?,?,?,'pending')""",
            (draft_id, kind, clause["id"], stable_no, body, note, author, baseline))
        proposal_id = cur.lastrowid

        if existing_pending == 0 and not changed_since_baseline:
            _apply_change(conn, draft_id, clause, kind, body, proposal_id, author)
            conn.commit()
            return {"proposal_id": proposal_id, "status": "applied", "pending": False}

        conn.commit()
        return {"proposal_id": proposal_id, "status": "pending",
                "pending": True,
                "reason": ("has_pending" if existing_pending else "baseline_conflict")}


def _clause_version_after(conn, clause_id, baseline):
    """该条款在版本 baseline 之后是否有生效修订（任一晚于基线即冲突）。"""
    row = conn.execute(
        "SELECT MAX(draft_version) AS v FROM clause_revisions "
        "WHERE clause_id=? AND rev>1 AND draft_version>?",
        (clause_id, baseline),
    ).fetchone()
    return row["v"] is not None


def _apply_change(conn, draft_id, clause, kind, body, proposal_id, applied_by):
    """把 replace/delete 提案落到草稿。调用方持写锁。"""
    draft = conn.execute("SELECT head_version FROM drafts WHERE id=?",
                         (draft_id,)).fetchone()
    new_version = draft["head_version"] + 1
    new_rev = _next_clause_rev(conn, clause["id"])

    if kind == KIND_DELETE:
        conn.execute(
            "UPDATE clauses SET is_active=0, current_rev=? WHERE id=?",
            (new_rev, clause["id"]))
        # 原意见保留原引用，标记失效，绝不转挂邻条
        conn.execute(
            "UPDATE comments SET is_active=0 WHERE clause_id=? AND is_active=1",
            (clause["id"],))
        rev_body = clause["body"]
    else:
        # 替换/合并文本：若条款曾被并行删除则随该修订恢复，但编号不变
        conn.execute(
            "UPDATE clauses SET body=?, is_active=1, current_rev=? WHERE id=?",
            (body, new_rev, clause["id"]))
        rev_body = body

    conn.execute(
        """INSERT INTO clause_revisions
           (clause_id, draft_id, rev, draft_version, kind, body, source_proposal_id)
           VALUES(?,?,?,?,?,?,?)""",
        (clause["id"], draft_id, new_rev, new_version, kind, rev_body, proposal_id))
    conn.execute(
        "UPDATE drafts SET head_version=? WHERE id=?", (new_version, draft_id))
    conn.execute(
        """UPDATE proposals SET status='applied', applied_at=?,
               applied_clause_rev=? WHERE id=?""",
        (now_ts(), new_rev, proposal_id))


# ---------------------------------------------------------------- 待决结案

def resolve_proposal(draft_id, proposal_id, data):
    resolution = _require_str(data, "resolution")
    if resolution not in ("keep", "take", "custom"):
        raise ServiceError("resolution 必须为 keep/take/custom",
                           status=422, code="invalid_field")
    editor = _require_str(data, "editor", max_len=100)

    custom_body = None
    if resolution == "custom":
        custom_body = _require_str(data, "body", max_len=100000)

    conn = db.get_conn()
    with _WRITE_LOCK:
        _get_draft(conn, draft_id)
        p = conn.execute(
            "SELECT * FROM proposals WHERE id=? AND draft_id=?",
            (proposal_id, draft_id),
        ).fetchone()
        if p is None:
            raise ServiceError(f"提案不存在: id={proposal_id}",
                               status=404, code="proposal_not_found")
        if p["status"] != "pending":
            raise ServiceError(f"提案已结案（当前状态 {p['status']}），不能重复处理",
                               status=409, code="not_pending")

        clause = conn.execute("SELECT * FROM clauses WHERE id=?",
                              (p["clause_id"],)).fetchone()
        ts = now_ts()

        if resolution == "keep":
            # 保留当前草稿，放弃本提案；不覆盖前写
            conn.execute(
                """UPDATE proposals SET status='closed', resolution='keep',
                       resolved_by=?, resolved_at=? WHERE id=?""",
                (editor, ts, proposal_id))

        elif resolution == "take":
            if p["kind"] == KIND_DELETE:
                if clause["is_active"]:
                    _apply_change(conn, draft_id, clause, KIND_DELETE, None,
                                  proposal_id, p["author"])
                # 若条款已被前写删除，则采纳删除即为现状，直接结案
            else:
                if clause["is_active"] and clause["body"] == p["body"]:
                    pass  # 内容与现状相同，无需再升版本
                else:
                    _apply_change(conn, draft_id, clause, KIND_REPLACE, p["body"],
                                  proposal_id, p["author"])
            conn.execute(
                """UPDATE proposals SET status='applied', resolution='take',
                       resolved_by=?, resolved_at=? WHERE id=?""",
                (editor, ts, proposal_id))

        else:  # custom：编辑者填写合并文本（对 replace/delete 待决都可恢复/替换）
            _apply_change(conn, draft_id, clause, KIND_REPLACE, custom_body,
                          proposal_id, editor)
            conn.execute(
                """UPDATE proposals SET status='applied', resolution='custom',
                       resolved_by=?, resolved_at=? WHERE id=?""",
                (editor, ts, proposal_id))

        conn.commit()
    return get_proposal(draft_id, proposal_id)


# ---------------------------------------------------------------- 发布快照

def publish_draft(draft_id, data):
    publisher = _require_str(data, "publisher", max_len=100)
    conn = db.get_conn()
    with _WRITE_LOCK:
        draft = _get_draft(conn, draft_id)
        pending = conn.execute(
            "SELECT COUNT(1) AS n FROM proposals WHERE draft_id=? AND status='pending'",
            (draft_id,),
        ).fetchone()["n"]
        if pending:
            raise ServiceError(f"尚有 {pending} 项待决未清，不能发布",
                               status=409, code="pending_exists")

        version = draft["head_version"]
        dup = conn.execute(
            "SELECT id FROM snapshots WHERE draft_id=? AND version=?",
            (draft_id, version),
        ).fetchone()
        if dup:
            return _snapshot_payload(conn, dup["id"])

        cur = conn.execute(
            """INSERT INTO snapshots(draft_id, version, published_by, title)
               VALUES(?,?,?,?)""",
            (draft_id, version, publisher, draft["title"]))
        snapshot_id = cur.lastrowid
        conn.execute(
            """INSERT INTO snapshot_clauses(snapshot_id, stable_no, body, rev)
               SELECT ?, stable_no, body, current_rev FROM clauses
               WHERE draft_id=? AND is_active=1 ORDER BY stable_no""",
            (snapshot_id, draft_id))
        conn.execute(
            "UPDATE drafts SET published_at=? WHERE id=?", (now_ts(), draft_id))
        conn.commit()
        return _snapshot_payload(conn, snapshot_id)


def _snapshot_payload(conn, snapshot_id):
    snap = conn.execute("SELECT * FROM snapshots WHERE id=?",
                        (snapshot_id,)).fetchone()
    rows = conn.execute(
        "SELECT stable_no, body, rev FROM snapshot_clauses WHERE snapshot_id=? "
        "ORDER BY id", (snapshot_id,)).fetchall()
    return {
        "snapshot_id": snap["id"],
        "draft_id": snap["draft_id"],
        "version": snap["version"],
        "title": snap["title"],
        "published_by": snap["published_by"],
        "created_at": snap["created_at"],
        "immutable": True,
        "clauses": [dict(r) for r in rows],
    }


# ---------------------------------------------------------------- 查询

def list_drafts():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT d.*,
                  (SELECT COUNT(1) FROM proposals WHERE draft_id=d.id AND status='pending')
                      AS pending_count,
                  (SELECT COUNT(1) FROM snapshots WHERE draft_id=d.id) AS snapshot_count
           FROM drafts d ORDER BY d.id DESC""").fetchall()
    return [dict(r) for r in rows]


def _clause_body_at(conn, clause_id, version):
    """条款在指定草案版本时的正文；版本 0 视为空基线。"""
    if version <= 0:
        return None
    row = conn.execute(
        """SELECT body FROM clause_revisions
           WHERE clause_id=? AND draft_version<=?
           ORDER BY draft_version DESC, rev DESC LIMIT 1""",
        (clause_id, version),
    ).fetchone()
    return row["body"] if row else None


def _proposal_brief(conn, row):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "stable_no": row["stable_no"],
        "note": row["note"],
        "author": row["author"],
        "baseline_version": row["baseline_version"],
        "status": row["status"],
        "resolution": row["resolution"],
        "resolved_by": row["resolved_by"],
        "created_at": row["created_at"],
        "applied_at": row["applied_at"],
        "resolved_at": row["resolved_at"],
        "body": row["body"],
    }


def get_draft(draft_id):
    conn = db.get_conn()
    draft = _get_draft(conn, draft_id)
    clauses = conn.execute(
        "SELECT * FROM clauses WHERE draft_id=? ORDER BY id", (draft_id,)).fetchall()

    clauses_out = []
    for c in sorted(clauses, key=lambda r: sort_key(r["stable_no"])):
        comments = conn.execute(
            "SELECT * FROM comments WHERE clause_id=? ORDER BY id",
            (c["id"],)).fetchall()
        # 来源：该条款各次生效修订对应提案
        revisions = conn.execute(
            """SELECT cr.*, p.author AS proposal_author, p.kind AS proposal_kind
               FROM clause_revisions cr
               LEFT JOIN proposals p ON p.id=cr.source_proposal_id
               WHERE cr.clause_id=? ORDER BY cr.rev""",
            (c["id"],)).fetchall()
        clauses_out.append({
            "id": c["id"],
            "stable_no": c["stable_no"],
            "body": c["body"],
            "is_active": bool(c["is_active"]),
            "current_rev": c["current_rev"],
            "comments": [{
                "id": m["id"],
                "author": m["author"],
                "content": m["content"],
                "is_active": bool(m["is_active"]),
                "created_at": m["created_at"],
                "ref_stable_no": m["stable_no"],
            } for m in comments],
            "sources": [{
                "rev": r["rev"],
                "draft_version": r["draft_version"],
                "kind": r["kind"],
                "author": r["proposal_author"] or draft["author"],
                "via_proposal_kind": r["proposal_kind"],
                "created_at": r["created_at"],
                "source_proposal_id": r["source_proposal_id"],
            } for r in revisions],
        })

    pending = conn.execute(
        "SELECT * FROM proposals WHERE draft_id=? AND status='pending' "
        "AND kind IN ('replace','delete') ORDER BY id",
        (draft_id,)).fetchall()
    pending_out = []
    for p in pending:
        current = conn.execute("SELECT * FROM clauses WHERE id=?",
                               (p["clause_id"],)).fetchone()
        pending_out.append({
            **_proposal_brief(conn, p),
            "current_body": current["body"] if current and current["is_active"] else None,
            "current_active": bool(current["is_active"]) if current else False,
        })

    history = conn.execute(
        """SELECT p.* FROM proposals p
           WHERE p.draft_id=? AND p.status!='pending'
           ORDER BY COALESCE(p.applied_at, p.resolved_at, p.created_at) DESC, p.id DESC""",
        (draft_id,)).fetchall()

    return {
        "id": draft["id"],
        "title": draft["title"],
        "author": draft["author"],
        "head_version": draft["head_version"],
        "published_at": draft["published_at"],
        "created_at": draft["created_at"],
        "clauses": clauses_out,
        "pending": pending_out,
        "history": [_proposal_brief(conn, p) for p in history],
        "pending_count": len(pending_out),
    }


def get_diff(draft_id, from_version, to_version):
    conn = db.get_conn()
    draft = _get_draft(conn, draft_id)
    if not (isinstance(from_version, int) and isinstance(to_version, int)
            and not isinstance(from_version, bool) and not isinstance(to_version, bool)):
        raise ServiceError("版本号必须为整数（0 表示空基线）",
                           status=422, code="invalid_field")
    head = draft["head_version"]
    if not (0 <= from_version < to_version <= head):
        raise ServiceError(
            f"版本范围非法：需满足 0 ≤ from < to ≤ v{head}（收到 {from_version}→{to_version}）",
            status=422, code="invalid_field")

    clause_rows = conn.execute("SELECT * FROM clauses WHERE draft_id=?",
                               (draft_id,)).fetchall()
    changes = []
    for c in sorted(clause_rows, key=lambda r: sort_key(r["stable_no"])):
        old = _clause_body_at(conn, c["id"], from_version)
        new = _clause_body_at(conn, c["id"], to_version)
        if old == new:
            continue
        if old is None and new is not None:
            kind = "added"
        elif old is not None and new is None:
            kind = "deleted"
        else:
            kind = "modified"
        patch = _unified_diff(
            old or "", new or "",
            f"{c['stable_no']}@v{from_version}",
            f"{c['stable_no']}@v{to_version}")
        changes.append({
            "stable_no": c["stable_no"],
            "change": kind,
            "old_body": old,
            "new_body": new,
            "diff": patch,
        })
    return {"draft_id": draft_id, "from_version": from_version,
            "to_version": to_version, "changes": changes}


def get_revisions(draft_id):
    conn = db.get_conn()
    _get_draft(conn, draft_id)
    rows = conn.execute(
        """SELECT cr.*, c.stable_no, p.author AS proposal_author
           FROM clause_revisions cr
           JOIN clauses c ON c.id=cr.clause_id
           LEFT JOIN proposals p ON p.id=cr.source_proposal_id
           WHERE cr.draft_id=? ORDER BY cr.draft_version DESC,
               cr.id DESC""",
        (draft_id,)).fetchall()
    return [{
        "id": r["id"],
        "stable_no": r["stable_no"],
        "rev": r["rev"],
        "draft_version": r["draft_version"],
        "kind": r["kind"],
        "author": r["proposal_author"],
        "source_proposal_id": r["source_proposal_id"],
        "created_at": r["created_at"],
    } for r in rows]


def get_proposal(draft_id, proposal_id):
    conn = db.get_conn()
    _get_draft(conn, draft_id)
    p = conn.execute("SELECT * FROM proposals WHERE id=? AND draft_id=?",
                     (proposal_id, draft_id)).fetchone()
    if p is None:
        raise ServiceError("提案不存在", status=404, code="proposal_not_found")
    return _proposal_brief(conn, p)


def list_snapshots(draft_id):
    conn = db.get_conn()
    _get_draft(conn, draft_id)
    rows = conn.execute(
        "SELECT id, draft_id, version, title, published_by, created_at "
        "FROM snapshots WHERE draft_id=? ORDER BY version DESC",
        (draft_id,)).fetchall()
    return [dict(r) for r in rows]


def get_snapshot(draft_id, snapshot_id):
    conn = db.get_conn()
    _get_draft(conn, draft_id)
    snap = conn.execute(
        "SELECT * FROM snapshots WHERE id=? AND draft_id=?",
        (snapshot_id, draft_id)).fetchone()
    if snap is None:
        raise ServiceError("快照不存在", status=404, code="snapshot_not_found")
    return _snapshot_payload(conn, snapshot_id)
