"""核心业务逻辑：版本管理、三方合并、冲突待决、意见失效、发布快照。

合并规则（无后写覆盖前写）：
  提案基于基线 B 提交，当前头版本为 H。
  - B == H：直接应用，生成新版本。
  - B != H 且提案触及的条款在 B 与 H 中内容一致：并行改动不冲突，合并生效。
  - 否则：进入 pending 待决，保留双方内容，由编辑者选择一方或填写合并文本结案。
"""
import re

from .db import get_conn

CLAUSE_RE = re.compile(r"^\d+(\.\d+)*$")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def clause_sort_key(number):
    return tuple(int(p) for p in number.split("."))


def sorted_clauses(state):
    """state: {number: text} -> 按编号自然排序的 [{number, text}] 列表。"""
    return [{"number": n, "text": state[n]} for n in sorted(state, key=clause_sort_key)]


# ---------------------------------------------------------------- 内部工具

def _version_state(conn, version_id):
    rows = conn.execute(
        "SELECT clause_number, text FROM clause_states WHERE version_id = ?",
        (version_id,),
    ).fetchall()
    return {r["clause_number"]: r["text"] for r in rows}


def _get_version(conn, draft_id, number):
    return conn.execute(
        "SELECT * FROM versions WHERE draft_id = ? AND number = ?",
        (draft_id, number),
    ).fetchone()


def _head_version(conn, draft_id):
    return conn.execute(
        "SELECT * FROM versions WHERE draft_id = ? ORDER BY number DESC LIMIT 1",
        (draft_id,),
    ).fetchone()


def _create_version(conn, draft_id, author, message, source, state):
    head = _head_version(conn, draft_id)
    number = 1 if head is None else head["number"] + 1
    cur = conn.execute(
        "INSERT INTO versions (draft_id, number, author, message, source) VALUES (?,?,?,?,?)",
        (draft_id, number, author, message, source),
    )
    vid = cur.lastrowid
    conn.executemany(
        "INSERT INTO clause_states (version_id, clause_number, text) VALUES (?,?,?)",
        [(vid, n, t) for n, t in state.items()],
    )
    return number


def _invalidate_comments(conn, draft_id, clause_number):
    """条款被删除：其上的意见保留原编号，标记为失效，绝不转挂邻条。"""
    conn.execute(
        "UPDATE comments SET status = 'invalid' "
        "WHERE draft_id = ? AND clause_number = ? AND status = 'active'",
        (draft_id, clause_number),
    )


def _require_draft(conn, draft_id):
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise ApiError(404, f"草案 {draft_id} 不存在")
    return row


def _require_base(conn, draft_id, base_version):
    try:
        base_version = int(base_version)
    except (TypeError, ValueError):
        raise ApiError(400, f"基线版本号无效: {base_version!r}")
    base = _get_version(conn, draft_id, base_version)
    if base is None:
        raise ApiError(400, f"基线版本 v{base_version} 不存在")
    return base


def _validate_clause_number(number):
    number = (number or "").strip()
    if not CLAUSE_RE.match(number):
        raise ApiError(400, f"条款编号无效: {number!r}（应为 1、2.1、3.1.4 这类数字编号）")
    return number


def _version_json(row):
    return {
        "number": row["number"],
        "author": row["author"],
        "message": row["message"],
        "source": row["source"],
        "source_label": _source_label(row["source"]),
        "created_at": row["created_at"],
    }


def _source_label(source):
    if source == "initial":
        return "初始创建"
    kind, _, sid = source.partition(":")
    if kind == "proposal":
        return f"提案 #{sid}"
    if kind == "resolution":
        return f"冲突结案 #{sid}"
    return source


def _proposal_json(row):
    return {
        "id": row["id"],
        "base_version": row["base_version"],
        "author": row["author"],
        "action": row["action"],
        "clause_number": row["clause_number"],
        "new_text": row["new_text"],
        "status": row["status"],
        "result_version": row["result_version"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }


# ---------------------------------------------------------------- 草案与版本

def create_draft(title, clauses, author="system"):
    title = (title or "").strip()
    if not title:
        raise ApiError(400, "标题不能为空")
    if not clauses:
        raise ApiError(400, "草案至少需要一个条款")
    state = {}
    for c in clauses:
        number = _validate_clause_number(c.get("number"))
        text = (c.get("text") or "").strip()
        if not text:
            raise ApiError(400, f"条款 {number} 的正文不能为空")
        if number in state:
            raise ApiError(400, f"条款编号重复: {number}")
        state[number] = text
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute("INSERT INTO drafts (title) VALUES (?)", (title,))
            draft_id = cur.lastrowid
            _create_version(conn, draft_id, author, "初始草案", "initial", state)
        return get_draft(draft_id)
    finally:
        conn.close()


def list_drafts():
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT d.id, d.title, d.created_at,
                   (SELECT MAX(number) FROM versions v WHERE v.draft_id = d.id) AS current_version,
                   (SELECT COUNT(*) FROM proposals p WHERE p.draft_id = d.id AND p.status = 'pending') AS pending_count
            FROM drafts d ORDER BY d.id
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_draft(draft_id):
    conn = get_conn()
    try:
        draft = _require_draft(conn, draft_id)
        head = _head_version(conn, draft_id)
        state = _version_state(conn, head["id"])
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM proposals WHERE draft_id = ? AND status = 'pending'",
            (draft_id,),
        ).fetchone()["c"]
        return {
            "id": draft["id"],
            "title": draft["title"],
            "created_at": draft["created_at"],
            "current_version": head["number"],
            "clauses": sorted_clauses(state),
            "pending_count": pending,
        }
    finally:
        conn.close()


def list_versions(draft_id):
    conn = get_conn()
    try:
        _require_draft(conn, draft_id)
        rows = conn.execute(
            "SELECT * FROM versions WHERE draft_id = ? ORDER BY number", (draft_id,)
        ).fetchall()
        return [_version_json(r) for r in rows]
    finally:
        conn.close()


def get_version(draft_id, number):
    conn = get_conn()
    try:
        _require_draft(conn, draft_id)
        ver = _get_version(conn, draft_id, number)
        if ver is None:
            raise ApiError(404, f"版本 v{number} 不存在")
        out = _version_json(ver)
        out["clauses"] = sorted_clauses(_version_state(conn, ver["id"]))
        return out
    finally:
        conn.close()


def diff_versions(draft_id, from_number, to_number):
    conn = get_conn()
    try:
        _require_draft(conn, draft_id)
        a = _get_version(conn, draft_id, from_number)
        b = _get_version(conn, draft_id, to_number)
        if a is None:
            raise ApiError(404, f"版本 v{from_number} 不存在")
        if b is None:
            raise ApiError(404, f"版本 v{to_number} 不存在")
        sa = _version_state(conn, a["id"])
        sb = _version_state(conn, b["id"])
        changes = []
        for num in sorted(set(sa) | set(sb), key=clause_sort_key):
            in_a, in_b = num in sa, num in sb
            if in_a and in_b:
                change = "modified" if sa[num] != sb[num] else "unchanged"
            elif in_a:
                change = "removed"
            else:
                change = "added"
            changes.append({
                "clause_number": num,
                "change": change,
                "old_text": sa.get(num),
                "new_text": sb.get(num),
            })
        return {"from": from_number, "to": to_number, "changes": changes}
    finally:
        conn.close()


# ---------------------------------------------------------------- 提案与合并

def submit_proposal(draft_id, base_version, author, action, clause_number, new_text):
    clause_number = _validate_clause_number(clause_number)
    author = (author or "").strip() or "anonymous"
    if action not in ("replace", "delete"):
        raise ApiError(400, "操作类型必须是 replace（替换）或 delete（删除）")
    if action == "replace":
        new_text = (new_text or "").strip()
        if not new_text:
            raise ApiError(400, "替换文本不能为空")
    else:
        new_text = None

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_draft(conn, draft_id)
        base = _require_base(conn, draft_id, base_version)
        base_state = _version_state(conn, base["id"])
        if clause_number not in base_state:
            raise ApiError(400, f"条款 {clause_number} 在基线版本 v{base['number']} 中不存在")

        head = _head_version(conn, draft_id)
        head_state = _version_state(conn, head["id"])

        # 同一基线之后该条款未被他人改动 -> 并行改动可合并；否则进入待决
        conflict = (
            head["number"] != base["number"]
            and head_state.get(clause_number) != base_state.get(clause_number)
        )

        if conflict:
            cur = conn.execute(
                "INSERT INTO proposals (draft_id, base_version, author, action, clause_number, new_text, status)"
                " VALUES (?,?,?,?,?,?,'pending')",
                (draft_id, base["number"], author, action, clause_number, new_text),
            )
            conn.commit()
            return {"status": "pending", "proposal_id": cur.lastrowid,
                    "message": "该条款已被他人基于同一基线修改，提案进入待决"}

        cur = conn.execute(
            "INSERT INTO proposals (draft_id, base_version, author, action, clause_number, new_text, status)"
            " VALUES (?,?,?,?,?,?,'merged')",
            (draft_id, base["number"], author, action, clause_number, new_text),
        )
        pid = cur.lastrowid
        new_state = dict(head_state)
        if action == "replace":
            new_state[clause_number] = new_text
        else:
            del new_state[clause_number]
        verb = "替换" if action == "replace" else "删除"
        number = _create_version(
            conn, draft_id, author, f"{verb}条款 {clause_number}", f"proposal:{pid}", new_state
        )
        conn.execute("UPDATE proposals SET result_version = ? WHERE id = ?", (number, pid))
        if action == "delete":
            _invalidate_comments(conn, draft_id, clause_number)
        conn.commit()
        return {"status": "merged", "proposal_id": pid, "version": number,
                "message": f"已合并，生成版本 v{number}"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_proposals(draft_id, status=None):
    conn = get_conn()
    try:
        _require_draft(conn, draft_id)
        sql = "SELECT * FROM proposals WHERE draft_id = ?"
        args = [draft_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY id"
        rows = conn.execute(sql, args).fetchall()
        out = []
        head = _head_version(conn, draft_id)
        head_state = _version_state(conn, head["id"])
        for r in rows:
            item = _proposal_json(r)
            if r["status"] == "pending":
                base_state = _version_state(conn, _get_version(conn, draft_id, r["base_version"])["id"])
                item["base_text"] = base_state.get(r["clause_number"])
                item["current_text"] = head_state.get(r["clause_number"])
            out.append(item)
        return out
    finally:
        conn.close()


def resolve_proposal(draft_id, proposal_id, choice, merged_text, author):
    author = (author or "").strip() or "anonymous"
    if choice not in ("ours", "theirs", "merged"):
        raise ApiError(400, "结案方式必须是 ours（保留当前）、theirs（采用提案）或 merged（合并文本）")
    if choice == "merged":
        merged_text = (merged_text or "").strip()
        if not merged_text:
            raise ApiError(400, "选择合并文本结案时必须填写合并后的条款文本")

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_draft(conn, draft_id)
        p = conn.execute(
            "SELECT * FROM proposals WHERE id = ? AND draft_id = ?",
            (proposal_id, draft_id),
        ).fetchone()
        if p is None:
            raise ApiError(404, f"提案 {proposal_id} 不存在")
        if p["status"] != "pending":
            raise ApiError(400, f"提案 #{proposal_id} 不在待决状态（当前状态: {p['status']}）")

        clause_number = p["clause_number"]
        if choice == "ours":
            conn.execute(
                "UPDATE proposals SET status = 'resolved-ours', resolved_at = datetime('now') WHERE id = ?",
                (proposal_id,),
            )
            conn.commit()
            return {"status": "resolved-ours", "message": "已保留当前内容，提案结案"}

        head = _head_version(conn, draft_id)
        new_state = _version_state(conn, head["id"])
        if choice == "theirs":
            if p["action"] == "replace":
                new_state[clause_number] = p["new_text"]
            else:
                new_state.pop(clause_number, None)
            message = f"冲突结案：采用提案 #{proposal_id}（{clause_number}）"
            status = "resolved-theirs"
        else:  # merged
            new_state[clause_number] = merged_text
            message = f"冲突结案：合并文本（{clause_number}）"
            status = "resolved-merged"

        number = _create_version(conn, draft_id, author, message, f"resolution:{proposal_id}", new_state)
        conn.execute(
            "UPDATE proposals SET status = ?, result_version = ?, resolved_at = datetime('now') WHERE id = ?",
            (status, number, proposal_id),
        )
        if clause_number not in new_state:
            _invalidate_comments(conn, draft_id, clause_number)
        conn.commit()
        return {"status": status, "version": number, "message": f"已结案，生成版本 v{number}"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- 意见

def add_comment(draft_id, base_version, clause_number, author, text):
    clause_number = _validate_clause_number(clause_number)
    author = (author or "").strip() or "anonymous"
    text = (text or "").strip()
    if not text:
        raise ApiError(400, "意见内容不能为空")
    conn = get_conn()
    try:
        with conn:
            _require_draft(conn, draft_id)
            base = _require_base(conn, draft_id, base_version)
            base_state = _version_state(conn, base["id"])
            if clause_number not in base_state:
                raise ApiError(400, f"条款 {clause_number} 在基线版本 v{base['number']} 中不存在")
            head_state = _version_state(conn, _head_version(conn, draft_id)["id"])
            status = "active" if clause_number in head_state else "invalid"
            cur = conn.execute(
                "INSERT INTO comments (draft_id, clause_number, base_version, author, text, status)"
                " VALUES (?,?,?,?,?,?)",
                (draft_id, clause_number, base["number"], author, text, status),
            )
            return {"id": cur.lastrowid, "status": status}
    finally:
        conn.close()


def list_comments(draft_id):
    conn = get_conn()
    try:
        _require_draft(conn, draft_id)
        rows = conn.execute(
            "SELECT * FROM comments WHERE draft_id = ? ORDER BY id", (draft_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- 发布

def publish(draft_id, author):
    author = (author or "").strip() or "anonymous"
    conn = get_conn()
    try:
        with conn:
            _require_draft(conn, draft_id)
            pending = conn.execute(
                "SELECT COUNT(*) AS c FROM proposals WHERE draft_id = ? AND status = 'pending'",
                (draft_id,),
            ).fetchone()["c"]
            if pending:
                raise ApiError(409, f"存在 {pending} 条未决冲突，不能发布")
            head = _head_version(conn, draft_id)
            state = _version_state(conn, head["id"])
            cur = conn.execute(
                "INSERT INTO publications (draft_id, version, author) VALUES (?,?,?)",
                (draft_id, head["number"], author),
            )
            pub_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO publication_clauses (publication_id, clause_number, text) VALUES (?,?,?)",
                [(pub_id, n, t) for n, t in state.items()],
            )
            return {"id": pub_id, "version": head["number"],
                    "message": f"已发布快照 #{pub_id}（基于 v{head['number']}），内容不可变"}
    finally:
        conn.close()


def list_publications(draft_id):
    conn = get_conn()
    try:
        _require_draft(conn, draft_id)
        rows = conn.execute(
            "SELECT * FROM publications WHERE draft_id = ? ORDER BY id", (draft_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_publication(draft_id, pub_id):
    conn = get_conn()
    try:
        _require_draft(conn, draft_id)
        pub = conn.execute(
            "SELECT * FROM publications WHERE id = ? AND draft_id = ?",
            (pub_id, draft_id),
        ).fetchone()
        if pub is None:
            raise ApiError(404, f"发布快照 {pub_id} 不存在")
        rows = conn.execute(
            "SELECT clause_number, text FROM publication_clauses WHERE publication_id = ?",
            (pub_id,),
        ).fetchall()
        state = {r["clause_number"]: r["text"] for r in rows}
        return {
            "id": pub["id"],
            "version": pub["version"],
            "author": pub["author"],
            "created_at": pub["created_at"],
            "clauses": sorted_clauses(state),
        }
    finally:
        conn.close()
