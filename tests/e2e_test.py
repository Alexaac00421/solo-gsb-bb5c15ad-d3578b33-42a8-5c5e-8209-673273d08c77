"""端到端业务规则验证（标准库 urllib，针对运行中的服务）。"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8123"
PASS, FAIL = 0, 0


def call(method, path, payload=None, expect=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read())
            status = r.status
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        status = e.code
    if expect is not None and status != expect:
        raise AssertionError(f"{method} {path} -> {status} (期望 {expect}): {body}")
    return status, body


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


# 1. 创建草案
_, d = call("POST", "/api/drafts", {
    "title": "测试规范", "author": "甲",
    "clauses": [{"stable_no": "1", "body": "条款一原文"},
                {"stable_no": "2", "body": "条款二原文"},
                {"stable_no": "A.10", "body": "附录十条"}]}, 201)
did = d["id"]
print(f"建稿 id={did}")

# 2. 非法创建不改变数据
before = call("GET", "/api/drafts")[1]
s, b = call("POST", "/api/drafts", {"title": "x", "author": "甲",
            "clauses": [{"stable_no": "1", "body": "a"}, {"stable_no": "1", "body": "b"}]}, 422)
s2, _ = call("POST", "/api/drafts", {"title": "  ", "author": "甲",
             "clauses": [{"stable_no": "1", "body": "a"}]}, 422)
s3, _ = call("POST", "/api/drafts", {"title": "好", "author": "甲", "clauses": []}, 422)
after = call("GET", "/api/drafts")[1]
check("重复编号/空标题/无条款全部 422 且草案列表不变", s == 422 and s2 == 422 and s3 == 422 and before == after)

# 3. 不存在基线（草案）
s, b = call("GET", "/api/drafts/999999", expect=404)
check("不存在草案 -> 404", s == 404 and b["error"] == "draft_not_found", str(b))

# 4. 同基线并行改【不同条款】→ 自动合并直接生效
_, r1 = call("POST", f"/api/drafts/{did}/proposals",
             {"kind": "replace", "stable_no": "1", "body": "条款一新",
              "author": "乙", "baseline_version": 1}, 201)
_, r2 = call("POST", f"/api/drafts/{did}/proposals",
             {"kind": "replace", "stable_no": "2", "body": "条款二新",
              "author": "丙", "baseline_version": 1}, 201)
check("不同条款并行改动各自生效", not r1["pending"] and not r2["pending"], str((r1, r2)))
draft = call("GET", f"/api/drafts/{did}")[1]
check("版本递增到 v3", draft["head_version"] == 3, str(draft["head_version"]))
bodies = {c["stable_no"]: c["body"] for c in draft["clauses"]}
check("两条新文本都在", bodies["1"] == "条款一新" and bodies["2"] == "条款二新")

# 5. 无效编号 / 无效版本基线
s, b = call("POST", f"/api/drafts/{did}/proposals",
            {"kind": "replace", "stable_no": "99", "body": "x", "author": "乙",
             "baseline_version": 3}, 422)
check("无效条款编号 -> 422", b["error"] == "invalid_stable_no")
s, b = call("POST", f"/api/drafts/{did}/proposals",
            {"kind": "replace", "stable_no": "1", "body": "x", "author": "乙",
             "baseline_version": 99}, 422)
check("不存在的基线版本 -> 422", b["error"] == "invalid_baseline")
s, b = call("POST", f"/api/drafts/{did}/proposals",
            {"kind": "replace", "stable_no": "bad no!", "body": "x",
             "author": "乙", "baseline_version": 3}, 422)
check("非法编号字符 -> 422", s == 422)
draft2 = call("GET", f"/api/drafts/{did}")[1]
check("被拒请求未改动草稿", draft2["head_version"] == 3 and
      {c["stable_no"]: c["body"] for c in draft2["clauses"]} == bodies)

# 6. 同条款并行冲突：两人都基于 v3 改条款 1
_, pA = call("POST", f"/api/drafts/{did}/proposals",
             {"kind": "replace", "stable_no": "1", "body": "条款一-A改",
              "author": "乙", "baseline_version": 3}, 201)
_, pB = call("POST", f"/api/drafts/{did}/proposals",
             {"kind": "replace", "stable_no": "1", "body": "条款一-B改",
              "author": "丙", "baseline_version": 3}, 201)
check("第一份同条款改动生效", not pA["pending"], str(pA))
check("第二份进入待决（后写不覆盖前写）", pB["pending"] is True, str(pB))
draft3 = call("GET", f"/api/drafts/{did}")[1]
b3 = {c["stable_no"]: c["body"] for c in draft3["clauses"]}
check("当前草稿保留先写内容(A)", b3["1"] == "条款一-A改", b3["1"])
check("待决列表含 B 的原文", any(x["id"] == pB["proposal_id"] and x["body"] == "条款一-B改"
                                for x in draft3["pending"]))

# 7. 待决未清禁止发布
s, b = call("POST", f"/api/drafts/{did}/publish", {"publisher": "秘书"}, 409)
check("有待决 -> 发布 409", b["error"] == "pending_exists", str(b))

# 8. 意见 + 删除条款 → 意见失效保留原引用，不可转挂/新附
call("POST", f"/api/drafts/{did}/proposals",
     {"kind": "comment", "stable_no": "A.10", "content": "请核对编号",
      "author": "丁", "baseline_version": draft3["head_version"]}, 201)
# 基于 v3 删除 A.10（当前是 v4，A.10 自 v3 未变 → 应直接生效）
_, pd = call("POST", f"/api/drafts/{did}/proposals",
             {"kind": "delete", "stable_no": "A.10",
              "author": "戊", "baseline_version": 3}, 201)
draft4 = call("GET", f"/api/drafts/{did}")[1]
a10 = next(c for c in draft4["clauses"] if c["stable_no"] == "A.10")
check("删除直接生效（不同条款）", not pd["pending"] and a10["is_active"] is False)
check("原意见保留且标记失效", len(a10["comments"]) == 1 and a10["comments"][0]["is_active"] is False
      and a10["comments"][0]["ref_stable_no"] == "A.10")
s, b = call("POST", f"/api/drafts/{did}/proposals",
            {"kind": "comment", "stable_no": "A.10", "content": "新意见",
             "author": "丁", "baseline_version": draft4["head_version"]}, 422)
check("已删除条款不能再附意见", s == 422)
s, b = call("POST", f"/api/drafts/{did}/proposals",
            {"kind": "delete", "stable_no": "A.10", "author": "戊",
             "baseline_version": draft4["head_version"]}, 422)
check("不能重复删除", s == 422)
# 编号不复用：同编号再做任何操作仍是“已删除/无效”
s, _ = call("POST", f"/api/drafts/{did}/proposals",
            {"kind": "replace", "stable_no": "A.10", "body": "冒充恢复",
             "author": "戊", "baseline_version": draft4["head_version"]}, 422)
check("已作废编号不能直接替换", s == 422)

# 9. 结案：合并文本 custom
_, res = call("POST", f"/api/drafts/{did}/proposals/{pB['proposal_id']}/resolve",
              {"resolution": "custom", "editor": "组长", "body": "条款一-AB合并"}, 200)
check("custom 结案生效", res["status"] == "applied" and res["resolution"] == "custom")
draft5 = call("GET", f"/api/drafts/{did}")[1]
b5 = {c["stable_no"]: c["body"] for c in draft5["clauses"]}
check("合并文本进入草稿", b5["1"] == "条款一-AB合并")
check("待决清零", draft5["pending_count"] == 0)
s, b = call("POST", f"/api/drafts/{did}/proposals/{pB['proposal_id']}/resolve",
            {"resolution": "keep", "editor": "组长"}, 409)
check("不能重复结案", s == 409)

# 10. 发布不可变快照；之后修改草稿不影响快照
v_at_pub = draft5["head_version"]
_, snap = call("POST", f"/api/drafts/{did}/publish", {"publisher": "秘书"}, 201)
check("快照版本=发布时版本", snap["version"] == v_at_pub, str(snap))
snap_clauses = {c["stable_no"] for c in snap["clauses"]}
check("快照不含已删除条款", "A.10" not in snap_clauses and "1" in snap_clauses)

# 草稿未变时重复发布：返回同一快照（幂等）
_, snap_same = call("POST", f"/api/drafts/{did}/publish", {"publisher": "秘书"}, 201)
check("同版本重复发布幂等", snap_same["snapshot_id"] == snap["snapshot_id"])

# 发布后继续编辑草稿
call("POST", f"/api/drafts/{did}/proposals",
     {"kind": "replace", "stable_no": "2", "body": "条款二发布后再改",
      "author": "乙", "baseline_version": v_at_pub}, 201)
_, snap_re = call("GET", f"/api/drafts/{did}/snapshots/{snap['snapshot_id']}")
snap_body_2 = next(c["body"] for c in snap_re["clauses"] if c["stable_no"] == "2")
check("快照冻结，后续编辑不影响快照", snap_body_2 == "条款二新", snap_body_2)
check("快照标记 immutable", snap_re["immutable"] is True)

# 新版本可再发布，产生独立快照
_, snap_again = call("POST", f"/api/drafts/{did}/publish", {"publisher": "秘书"}, 201)
check("新版本产生新快照且各自冻结", snap_again["snapshot_id"] != snap["snapshot_id"]
      and snap_again["version"] == v_at_pub + 1
      and snap["version"] == v_at_pub)

# 11. 差异
_, diff = call("GET", f"/api/drafts/{did}/diff?from=0&to=3")
nos = {ch["stable_no"] for ch in diff["changes"]}
check("v0→v3 差异含全部初始条款", nos == {"1", "2", "A.10"}, str(nos))
_, diff34 = call("GET", f"/api/drafts/{did}/diff?from=3&to=4")
check("v3→v4 仅含条款1", {ch["stable_no"] for ch in diff34["changes"]} == {"1"})
check("差异为 unified diff", any(ch["diff"].startswith("---") for ch in diff["changes"]))
s, _ = call("GET", f"/api/drafts/{did}/diff?from=abc&to=2", 422)
check("差异版本号非法 -> 422", s == 422)

# 12. 修订来源
_, revs = call("GET", f"/api/drafts/{did}/revisions")
rev1 = next(r for r in revs["revisions"] if r["stable_no"] == "1" and r["kind"] == "replace"
            and r["source_proposal_id"] is not None and r["author"] == "乙")
check("修订带来源提案与编辑者", bool(rev1))

# 13. keep / take 路径：再造冲突并分别验证
_, k1 = call("POST", f"/api/drafts/{did}/proposals",
             {"kind": "replace", "stable_no": "1", "body": "keep-test",
              "author": "乙", "baseline_version": draft5["head_version"]}, 201)
_, k2 = call("POST", f"/api/drafts/{did}/proposals",
             {"kind": "replace", "stable_no": "1", "body": "take-test",
              "author": "丙", "baseline_version": draft5["head_version"]}, 201)
check("又一次同条款冲突入待决", k2["pending"] is True, str(k2))
call("POST", f"/api/drafts/{did}/proposals/{k2['proposal_id']}/resolve",
     {"resolution": "take", "editor": "组长"}, 200)
dk = call("GET", f"/api/drafts/{did}")[1]
check("take 后采用提案文本", next(c for c in dk["clauses"] if c["stable_no"] == "1")["body"] == "take-test")
# k1 此时状态应为 applied（它先写生效）
check("先写提案保持 applied", any(h["id"] == k1["proposal_id"] and h["status"] == "applied"
                                  for h in dk["history"]))

# 14. 并行删除与替换互撞：删除先生效，替换（基于旧基线）进入待决，custom 可恢复
base = dk["head_version"]
call("POST", f"/api/drafts/{did}/proposals",
     {"kind": "delete", "stable_no": "2", "author": "乙", "baseline_version": base}, 201)
_, coll = call("POST", f"/api/drafts/{did}/proposals",
               {"kind": "replace", "stable_no": "2", "body": "并行替换2",
                "author": "丙", "baseline_version": base}, 201)
check("删除先生效、并行替换入待决（不丢失、不报错）", coll["pending"] is True, str(coll))
call("POST", f"/api/drafts/{did}/proposals/{coll['proposal_id']}/resolve",
     {"resolution": "custom", "editor": "组长", "body": "条款二恢复版"}, 200)
dc = call("GET", f"/api/drafts/{did}")[1]
c2 = next(c for c in dc["clauses"] if c["stable_no"] == "2")
check("custom 以原编号恢复条款", c2["is_active"] and c2["body"] == "条款二恢复版")

print(f"\n结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
