"""端到端冒烟测试：直接调用业务逻辑，覆盖题目要求的关键行为。

运行：python3 tests/smoke_test.py
"""
import os
import sys
import tempfile

# 使用临时数据库，避免污染真实数据
_tmp = tempfile.mkdtemp()
os.environ["APP_DB"] = os.path.join(_tmp, "test.db")
os.environ["SEED_DEMO"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import logic  # noqa: E402
from app.db import init_db  # noqa: E402
from app.logic import ApiError  # noqa: E402

PASSED = 0


def check(name, cond):
    global PASSED
    assert cond, f"FAILED: {name}"
    PASSED += 1
    print(f"  ok - {name}")


def expect_error(name, status, fn):
    try:
        fn()
    except ApiError as e:
        check(f"{name}（报 {e.status}: {e.message}）", e.status == status)
        return
    raise AssertionError(f"FAILED: {name} 未报错")


init_db()

print("== 创建草案 ==")
d = logic.create_draft("测试标准 第1部分", [
    {"number": "1", "text": "范围原文"},
    {"number": "2", "text": "引用原文"},
    {"number": "3.1", "text": "术语A原文"},
    {"number": "3.2", "text": "术语B原文"},
], author="alice")
did = d["id"]
check("初始版本为 v1", d["current_version"] == 1)
check("初始条款数", len(d["clauses"]) == 4)

expect_error("无效编号建草案", 400, lambda: logic.create_draft("坏", [{"number": "a.b", "text": "x"}]))
expect_error("重复编号建草案", 400, lambda: logic.create_draft(
    "坏", [{"number": "1", "text": "x"}, {"number": "1", "text": "y"}]))

print("== 基于当前版本的修订直接生效 ==")
r = logic.submit_proposal(did, 1, "alice", "replace", "1", "范围修订一")
check("替换合并", r["status"] == "merged" and r["version"] == 2)

print("== 同一基线上的并行改动：不同条款应合并 ==")
# bob 和 carol 都基于 v2 提交，触及不同条款
r1 = logic.submit_proposal(did, 2, "bob", "replace", "2", "引用-bob")
r2 = logic.submit_proposal(did, 2, "carol", "replace", "3.1", "术语A-carol")
check("bob 基于 v2 合并", r1["status"] == "merged" and r1["version"] == 3)
check("carol 基于同一基线 v2、触及不同条款，自动合并", r2["status"] == "merged" and r2["version"] == 4)
d = logic.get_draft(did)
texts = {c["number"]: c["text"] for c in d["clauses"]}
check("两处并行改动都保留", texts["2"] == "引用-bob" and texts["3.1"] == "术语A-carol")

print("== 同一基线上的并行改动：同一条款进入待决，不得后写覆盖前写 ==")
r3 = logic.submit_proposal(did, 4, "dave", "replace", "3.2", "术语B-dave")   # v5
r4 = logic.submit_proposal(did, 4, "erin", "replace", "3.2", "术语B-erin")   # 冲突
check("dave 先合并", r3["status"] == "merged" and r3["version"] == 5)
check("erin 同条款冲突进入待决", r4["status"] == "pending")
d = logic.get_draft(did)
texts = {c["number"]: c["text"] for c in d["clauses"]}
check("当前内容仍是 dave 的（未被后写覆盖）", texts["3.2"] == "术语B-dave")
check("待决计数为 1", d["pending_count"] == 1)

pend = logic.list_proposals(did, status="pending")
check("待决保留双方内容", pend[0]["current_text"] == "术语B-dave" and pend[0]["new_text"] == "术语B-erin")

print("== 待决未清不能发布 ==")
expect_error("待决未清发布被拒", 409, lambda: logic.publish(did, "alice"))

print("== 编辑者以合并文本结案 ==")
r = logic.resolve_proposal(did, r4["proposal_id"], "merged", "术语B-dave+erin合并", "alice")
check("结案生成新版本", r["version"] == 6)
d = logic.get_draft(did)
texts = {c["number"]: c["text"] for c in d["clauses"]}
check("合并文本生效", texts["3.2"] == "术语B-dave+erin合并")
check("待决清零", d["pending_count"] == 0)
expect_error("重复结案被拒", 400, lambda: logic.resolve_proposal(did, r4["proposal_id"], "ours", None, "alice"))

print("== 意见附着稳定编号；删除条款后保留原引用并标失效 ==")
c1 = logic.add_comment(did, 6, "2", "bob", "建议补充引用清单")
check("意见为 active", c1["status"] == "active")
r = logic.submit_proposal(did, 6, "alice", "delete", "2", None)
check("删除条款合并", r["status"] == "merged")
comments = logic.list_comments(did)
check("意见保留原编号 2", comments[0]["clause_number"] == "2")
check("意见标记为失效", comments[0]["status"] == "invalid")
d = logic.get_draft(did)
check("条款 2 已删除", all(c["number"] != "2" for c in d["clauses"]))
check("意见未转挂邻条（编号仍是 2 而非 3.1）", comments[0]["clause_number"] == "2")

print("== 无效编号与不存在的基线报错且不改变草稿 ==")
before = logic.get_draft(did)["current_version"]
expect_error("无效条款编号", 400, lambda: logic.submit_proposal(did, 7, "x", "replace", "abc", "y"))
expect_error("不存在的条款", 400, lambda: logic.submit_proposal(did, 7, "x", "replace", "9.9", "y"))
expect_error("不存在的基线", 400, lambda: logic.submit_proposal(did, 999, "x", "replace", "1", "y"))
expect_error("无效基线号", 400, lambda: logic.submit_proposal(did, "v7", "x", "replace", "1", "y"))
expect_error("意见引用无效编号", 400, lambda: logic.add_comment(did, 7, "x.y", "a", "b"))
expect_error("意见引用不存在的基线", 400, lambda: logic.add_comment(did, 999, "1", "a", "b"))
check("草稿版本未变", logic.get_draft(did)["current_version"] == before)

print("== 发布生成不可变快照，后续编辑仅影响草稿 ==")
pub = logic.publish(did, "alice")
check("发布成功", pub["version"] == logic.get_draft(did)["current_version"])
snap_before = logic.get_publication(did, pub["id"])
logic.submit_proposal(did, pub["version"], "bob", "replace", "1", "范围-发布后再改")
snap_after = logic.get_publication(did, pub["id"])
check("快照内容不变", snap_before["clauses"] == snap_after["clauses"])
check("草稿继续前进", logic.get_draft(did)["current_version"] == pub["version"] + 1)
pubs = logic.list_publications(did)
check("快照列表含 1 条", len(pubs) == 1)

print("== 版本差异与修订来源 ==")
diff = logic.diff_versions(did, 1, 2)
chg = {c["clause_number"]: c["change"] for c in diff["changes"]}
check("diff 检出修改", chg.get("1") == "modified" and chg.get("2") == "unchanged")
versions = logic.list_versions(did)
check("版本来源可溯（提案）", any(v["source"].startswith("proposal:") for v in versions))
check("版本来源可溯（结案）", any(v["source"].startswith("resolution:") for v in versions))
expect_error("不存在的版本 diff", 404, lambda: logic.diff_versions(did, 1, 999))

print("== theirs / ours 结案路径 ==")
logic.submit_proposal(did, 8, "alice", "replace", "3.1", "术语A-v9")          # v9
rA = logic.submit_proposal(did, 8, "bob", "replace", "3.1", "术语A-bob")      # 冲突
rB = logic.submit_proposal(did, 8, "carol", "delete", "3.1", None)            # 冲突
check("两个提案均待决", rA["status"] == "pending" and rB["status"] == "pending")
logic.resolve_proposal(did, rA["proposal_id"], "theirs", None, "alice")       # 采用提案 -> v10
check("theirs 结案生效", {c["number"]: c["text"] for c in logic.get_draft(did)["clauses"]}["3.1"] == "术语A-bob")
logic.resolve_proposal(did, rB["proposal_id"], "ours", None, "alice")         # 保留当前，无新版本
check("ours 结案不产生新版本", logic.get_draft(did)["current_version"] == 10)
check("ours 结案保留当前内容", {c["number"]: c["text"] for c in logic.get_draft(did)["clauses"]}["3.1"] == "术语A-bob")

print(f"\n全部通过：{PASSED} 项检查")
