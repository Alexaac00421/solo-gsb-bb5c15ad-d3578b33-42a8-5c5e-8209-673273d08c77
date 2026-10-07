"""HTTP 接口与静态页面。启动时自动建库建表，并按需写入演示数据。"""
import os

from flask import Flask, jsonify, request, send_from_directory

from . import logic
from .db import get_conn, init_db
from .logic import ApiError

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def create_app():
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    init_db()
    _maybe_seed()

    @app.errorhandler(ApiError)
    def handle_api_error(err):
        return jsonify({"error": err.message}), err.status

    @app.errorhandler(404)
    def handle_404(_):
        return jsonify({"error": "资源不存在"}), 404

    # ---------------- 页面 ----------------
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    # ---------------- 草案 ----------------
    @app.get("/api/drafts")
    def drafts():
        return jsonify(logic.list_drafts())

    @app.post("/api/drafts")
    def create_draft():
        body = request.get_json(force=True)
        return jsonify(logic.create_draft(
            body.get("title"),
            body.get("clauses"),
            author=body.get("author") or "system",
        )), 201

    @app.get("/api/drafts/<int:draft_id>")
    def draft_detail(draft_id):
        return jsonify(logic.get_draft(draft_id))

    # ---------------- 版本与差异 ----------------
    @app.get("/api/drafts/<int:draft_id>/versions")
    def versions(draft_id):
        return jsonify(logic.list_versions(draft_id))

    @app.get("/api/drafts/<int:draft_id>/versions/<int:number>")
    def version_detail(draft_id, number):
        return jsonify(logic.get_version(draft_id, number))

    @app.get("/api/drafts/<int:draft_id>/diff")
    def diff(draft_id):
        return jsonify(logic.diff_versions(
            draft_id,
            int(request.args.get("from", "0")),
            int(request.args.get("to", "0")),
        ))

    # ---------------- 提案与待决 ----------------
    @app.post("/api/drafts/<int:draft_id>/proposals")
    def submit_proposal(draft_id):
        body = request.get_json(force=True)
        return jsonify(logic.submit_proposal(
            draft_id,
            body.get("base_version"),
            body.get("author"),
            body.get("action"),
            body.get("clause_number"),
            body.get("new_text"),
        )), 201

    @app.get("/api/drafts/<int:draft_id>/proposals")
    def proposals(draft_id):
        return jsonify(logic.list_proposals(draft_id, request.args.get("status")))

    @app.post("/api/drafts/<int:draft_id>/proposals/<int:proposal_id>/resolve")
    def resolve(draft_id, proposal_id):
        body = request.get_json(force=True)
        return jsonify(logic.resolve_proposal(
            draft_id,
            proposal_id,
            body.get("choice"),
            body.get("merged_text"),
            body.get("author"),
        ))

    # ---------------- 意见 ----------------
    @app.post("/api/drafts/<int:draft_id>/comments")
    def add_comment(draft_id):
        body = request.get_json(force=True)
        return jsonify(logic.add_comment(
            draft_id,
            body.get("base_version"),
            body.get("clause_number"),
            body.get("author"),
            body.get("text"),
        )), 201

    @app.get("/api/drafts/<int:draft_id>/comments")
    def comments(draft_id):
        return jsonify(logic.list_comments(draft_id))

    # ---------------- 发布 ----------------
    @app.post("/api/drafts/<int:draft_id>/publish")
    def publish(draft_id):
        body = request.get_json(force=True) if request.data else {}
        return jsonify(logic.publish(draft_id, body.get("author"))), 201

    @app.get("/api/drafts/<int:draft_id>/publications")
    def publications(draft_id):
        return jsonify(logic.list_publications(draft_id))

    @app.get("/api/drafts/<int:draft_id>/publications/<int:pub_id>")
    def publication_detail(draft_id, pub_id):
        return jsonify(logic.get_publication(draft_id, pub_id))

    return app


def _maybe_seed():
    """数据库为空时写入一个演示草案，便于启动后直接查看（SEED_DEMO=0 可关闭）。"""
    if os.environ.get("SEED_DEMO", "1") == "0":
        return
    conn = get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM drafts").fetchone()["c"]
    finally:
        conn.close()
    if count:
        return
    demo = logic.create_draft(
        "智慧城市 数据交换 第1部分：总体要求（演示草案）",
        [
            {"number": "1", "text": "本部分规定了智慧城市数据交换的总体架构、基本要求与术语。"},
            {"number": "2", "text": "下列文件中的内容通过文中的规范性引用而构成本文件必不可少的条款。"},
            {"number": "3", "text": "下列术语和定义适用于本文件。"},
            {"number": "3.1", "text": "数据交换 data exchange：不同系统之间按照约定格式进行数据传递的过程。"},
            {"number": "3.2", "text": "参与方 participant：参与数据交换的提供方、使用方与平台运营方。"},
            {"number": "4", "text": "数据交换应采用分层架构，包括接入层、交换层与应用层。"},
        ],
        author="system",
    )
    logic.add_comment(demo["id"], 1, "3.1", "system", "建议补充英文缩略语对照。")


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("APP_PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
