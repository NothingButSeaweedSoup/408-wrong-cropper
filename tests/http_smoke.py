"""HTTP 全链路冒烟测试（只用标准库，不依赖 requests）。

前置：另开一个终端启动服务 .venv\\Scripts\\python.exe -m backend   （端口默认 18100）
跑法：.venv\\Scripts\\python.exe tests\\http_smoke.py [http://127.0.0.1:18100]

覆盖：
- 管理接口鉴权（无密钥 401 / 错密钥 401 / 正确密钥通过 / 公开接口不受影响）
- 上传 -> 轮询 -> 页面图/题目图 -> 改边界重裁 -> 切分 -> 合并 -> 导出 Word -> 下载校验 -> 清理
"""

from __future__ import annotations

import http.cookiejar
import io
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18100"
ok = True


def config_cookie_name() -> str:
    """登录 Cookie 名（跟 backend/config.py 的 TOKEN_COOKIE 保持一致）。"""
    sys.path.insert(0, str(ROOT))
    from backend import config  # noqa: PLC0415

    return config.TOKEN_COOKIE


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and bool(condition)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


def read_admin_key() -> str:
    """从 backend/.env 读管理密钥（没有就读环境变量）。"""
    for candidate in (ROOT / "backend" / ".env", ROOT / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("ZC_ADMIN_KEY=") or line.startswith("ADMIN_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ZC_ADMIN_KEY", "")


ADMIN_KEY = read_admin_key()


def call(method: str, path: str, *, payload: dict | None = None, raw: bytes | None = None,
         content_type: str | None = None, binary: bool = False, key: str | None = None,
         token: str = ""):
    """key=None 用 .env 里的管理员密钥；key="" 表示故意不带密钥。token 用于用户接口。"""
    url = path if path.startswith("http") else BASE + path
    data = raw
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    use_key = ADMIN_KEY if key is None else key
    if use_key:
        headers["X-Admin-Key"] = use_key
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read()
        if binary:
            return resp.status, resp.headers, body
        return json.loads(body) if body else None


def status_of(method: str, path: str, key: str) -> int:
    try:
        call(method, path, key=key)
        return 200
    except urllib.error.HTTPError as exc:
        return exc.code


def upload(path: Path, year: int, title: str) -> dict:
    boundary = f"----zc{uuid.uuid4().hex}"
    buf = io.BytesIO()

    def field(name: str, value: str) -> None:
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())

    field("year", str(year))
    field("title", title)
    ctype = mimetypes.guess_type(path.name)[0] or "application/pdf"
    buf.write(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode()
    )
    buf.write(path.read_bytes())
    buf.write(f"\r\n--{boundary}--\r\n".encode())
    return call("POST", "/api/papers", raw=buf.getvalue(),
                content_type=f"multipart/form-data; boundary={boundary}")


def main() -> int:
    print(f"目标服务：{BASE}")
    print(f"管理密钥：{'已从 backend/.env 读取' if ADMIN_KEY else '未找到（管理接口会全部 401）'}")

    print("\n0) 服务健康检查")
    try:
        health = call("GET", "/api/health")
        check("health 正常", health.get("ok") is True, f"port={health.get('port')}")
    except Exception as exc:  # noqa: BLE001
        check("服务可达", False, f"{exc}（请先启动 .venv\\Scripts\\python.exe -m backend）")
        return 1
    if not ADMIN_KEY:
        print("  没有密钥，后续管理接口测试无法继续")
        return 1

    print("\n0.5) 鉴权：公开 / 需登录 / 需管理员")
    check("公开接口 /api/meta 不需要登录", status_of("GET", "/api/meta", "") == 200)
    check("公开接口 /api/health 不需要登录", status_of("GET", "/api/health", "") == 200)
    check("公开接口 /api/auth/status 不需要登录", status_of("GET", "/api/auth/status", "") == 200)
    check("未登录读题目库 -> 401", status_of("GET", "/api/questions", "") == 401)
    check("未登录读 catalog -> 401", status_of("GET", "/api/catalog", "") == 401)
    check("未登录导出 Word -> 401", status_of("POST", "/api/export", "") == 401)
    check("未登录读导出历史 -> 401", status_of("GET", "/api/exports", "") == 401)
    check("未登录读真题列表 -> 401", status_of("GET", "/api/papers", "") == 401)
    check("错误密钥访问 /api/papers -> 401", status_of("GET", "/api/papers", "wrong-key") == 401)
    check("兜底密钥可访问 /api/papers", isinstance(call("GET", "/api/papers"), list))
    check("兜底密钥可通过 /api/admin/verify", call("GET", "/api/admin/verify").get("ok") is True)

    sample = ROOT / "data" / "_selftest" / "uploads" / "sample_2009.pdf"
    if not sample.exists():
        print("  样本 PDF 不存在，请先跑 tests/e2e.py 生成")
        return 1

    print("\n0.6) 用户系统与得分记录")
    check("未登录读 /api/scores 返回 401", status_of("GET", "/api/scores", "") == 401)
    check("未登录写 /api/scores 返回 401", status_of("POST", "/api/scores", "") == 401)
    check("注册开关状态可公开读取", "allow_register" in call("GET", "/api/auth/status", key=""))

    suffix = str(int(time.time()) % 1000000)
    username = f"smoke{suffix}"
    registered = call("POST", "/api/auth/register", key="",
                      payload={"username": username, "password": "secret123", "display_name": "冒烟用户"})
    user_token = registered["token"]
    user_id = registered["user"]["id"]
    check(f"注册 {username} 并拿到 token", bool(user_token) and user_id > 0)

    me = call("GET", "/api/auth/me", key="", token=user_token)
    check("token 能取回自己", me["user"]["username"] == username, me["user"]["display_name"])

    try:
        call("POST", "/api/auth/login", key="", payload={"username": username, "password": "wrong-pass"})
        check("错误密码被拒", False, "竟然登录成功了")
    except urllib.error.HTTPError as exc:
        check("错误密码被拒 401", exc.code == 401, str(exc.code))
    check("正确密码能登录", bool(call("POST", "/api/auth/login", key="",
                                     payload={"username": username, "password": "secret123"})["token"]))

    schema = call("GET", "/api/scores/schema", key="", token=user_token)
    check("没传年份时给默认结构（4 组选择题 + 7 道综合题）",
          schema["source"] == "default" and len(schema["choice_groups"]) == 4 and len(schema["subjective"]) == 7,
          f'{schema["source"]} {len(schema["choice_groups"])}/{len(schema["subjective"])}')
    # 用**没导入过试卷的年份**，免得库里已有真题时结构对不上（下面第 2.6 步才专门测"按卷面结构录"）
    YEAR_A, YEAR_B = 1995, 1996
    expect_full = schema["module_full"]["total"]
    full = {"paper_year": YEAR_A, "practice_date": "2026-09-17",
            "choice": {g["key"]: g["count"] for g in schema["choice_groups"]},
            "subjective": [{"qno": s["qno"], "score": s["full"], "full": s["full"]} for s in schema["subjective"]],
            "note": "冒烟测试"}
    perfect = call("POST", "/api/scores", key="", token=user_token, payload=full)
    check(f"全对记录 = 满分 {expect_full}", perfect["total_score"] == expect_full, str(perfect["total_score"]))
    partial = call("POST", "/api/scores", key="", token=user_token, payload={
        "paper_year": YEAR_B, "practice_date": "2026-09-25",
        "choice": {g["key"]: max(0, g["count"] - 2) for g in schema["choice_groups"]},
        "subjective": [{"qno": s["qno"], "score": round(s["full"] / 2, 1), "full": s["full"]} for s in schema["subjective"]],
    })
    check("半对记录分数合理", 0 < partial["total_score"] < expect_full, str(partial["total_score"]))

    sec = perfect["sections"]
    check("客观题：80/80 = 100%",
          sec["objective"] == {"score": 80.0, "full": 80.0, "rate": 100.0}, str(sec["objective"]))
    check("主观题：70/70 = 100%",
          sec["subjective"] == {"score": 70.0, "full": 70.0, "rate": 100.0}, str(sec["subjective"]))
    check("合计 = 客观 + 主观",
          sec["total"]["score"] == sec["objective"]["score"] + sec["subjective"]["score"]
          and sec["total"]["score"] == perfect["total_score"], str(sec["total"]))
    psec = partial["sections"]
    check("半对记录：主观题的分算进总分了",
          psec["subjective"]["score"] > 0
          and round(psec["objective"]["score"] + psec["subjective"]["score"], 1) == partial["total_score"],
          f'{psec["objective"]["score"]} + {psec["subjective"]["score"]} = {partial["total_score"]}')

    records = call("GET", "/api/scores", key="", token=user_token)
    check("列表 2 条且按时间倒序", len(records) == 2 and records[0]["practice_date"] == "2026-09-25",
          str([r["practice_date"] for r in records]))
    check("带模块得分率", records[0]["rates"]["ds"] > 0 and records[0]["total_full"] == expect_full)
    check("列表里也带客观/主观/合计",
          records[0]["sections"]["total"]["rate"] == records[0]["total_rate"],
          str(records[0]["sections"]["total"]))

    trend = call("GET", "/api/scores/trend?x_axis=practice_date", key="", token=user_token)
    check("时间轴 2 个点", len(trend["labels"]) == 2, str(trend["labels"]))
    check("模块百分比 + 总分绝对分",
          trend["series"]["ds"][0] == 100.0 and trend["series"]["total"][0] == expect_full)
    year_trend = call("GET", "/api/scores/trend?x_axis=paper_year&aggregate=latest", key="", token=user_token)
    check("年份轴按年份排序", year_trend["labels"] == [str(YEAR_A), str(YEAR_B)], str(year_trend["labels"]))

    estimate = trend["estimate"]
    check("趋势响应里带「当前水平估计」", estimate["count"] == 2, str(estimate["count"]))
    check("不足 3 次时权重归一化（50/85、35/85）",
          abs(estimate["weights"][0] - 50 / 85) < 1e-4 and abs(estimate["weights"][1] - 35 / 85) < 1e-4,
          str(estimate["weights"]))
    check("估计总分介于两次成绩之间", 95 < estimate["total"] < 150, str(estimate["total"]))
    check("估计里模块是得分率、总分是原始分",
          all(0 <= v <= 100 for v in estimate["modules"].values()) and estimate["total_full"] == expect_full,
          f'{estimate["modules"]} total={estimate["total"]}')
    check("估计样本按时间倒序", [s["practice_date"] for s in estimate["samples"]] == ["2026-09-25", "2026-09-17"],
          str([s["practice_date"] for s in estimate["samples"]]))
    check("水平估计里客观/主观分开给比例",
          set(estimate["sections"]) == {"objective", "subjective"} and estimate["sections"]["objective"] > 0,
          str(estimate["sections"]))

    edited = call("PUT", f"/api/scores/{partial['id']}", key="", token=user_token, payload={
        **full, "practice_date": "2026-09-26", "note": "改过"})
    check("编辑后重算并更新日期",
          edited["practice_date"] == "2026-09-26" and edited["total_score"] == expect_full,
          f'{edited["practice_date"]} / {edited["total_score"]}')
    call("DELETE", f"/api/scores/{edited['id']}", key="", token=user_token)
    check("删除后只剩 1 条", len(call("GET", "/api/scores", key="", token=user_token)) == 1)

    check("管理员能看到用户列表", any(u["id"] == user_id for u in call("GET", "/api/admin/users")))
    check("用户接口不接受管理员密钥当登录态",
          status_of("GET", "/api/scores", ADMIN_KEY) == 401)
    try:
        call("GET", "/api/papers", key="", token=user_token)
        check("普通用户不能进管理接口", False, "竟然进去了")
    except urllib.error.HTTPError as exc:
        check("普通用户进管理接口得到 403（提示怎么加管理员）", exc.code == 403, str(exc.code))
    check("管理后台用密钥仍可登记登录态",
          call("GET", "/api/admin/verify")["via"] == "key")

    print("\n1) 上传真题 PDF")
    paper = upload(sample, 2009, "2009 年 408 真题（HTTP 冒烟）")
    paper_id = paper["id"]
    check(f"创建 paper id={paper_id}", paper_id > 0, f"status={paper['status']}")

    print("\n2) 轮询处理进度")
    detail = {}
    for _ in range(150):
        detail = call("GET", f"/api/papers/{paper_id}")
        if detail["status"] in ("split", "failed"):
            break
        time.sleep(2)
    check("处理完成 status=split", detail.get("status") == "split", detail.get("message", ""))
    check("渲染 2 页", len(detail.get("pages") or []) == 2, str(len(detail.get("pages") or [])))
    questions = detail.get("questions") or []
    check("切出 5 道题", len(questions) == 5, str(len(questions)))
    q3 = next((q for q in questions if q["question_no"] == 3), None)
    check("第 3 题为跨页题", bool(q3) and len(q3["bbox"]["blocks"]) == 2,
          str(len(q3["bbox"]["blocks"])) if q3 else "无")
    check("题目带 image_urls", all(q["image_urls"] for q in questions))

    print("\n2.5) 卷面结构（各年题号范围/分值不同，所以结构挂在试卷上）")
    structure = call("GET", f"/api/papers/{paper_id}/structure")
    check("切完题自动生成了结构", structure["source"] == "auto" and bool(structure["choice"]),
          f'{structure["source"]} / {len(structure["choice"])} 组')
    check("结构覆盖切出来的题号（1-5）",
          sorted(q for g in structure["choice"] for q in range(g["from"], g["to"] + 1)) == [1, 2, 3, 4, 5],
          str(structure["choice"]))
    check("第 4 题卷面印的 (8分) 被读进结构",
          any(g["per_score"] == 8.0 for g in structure["choice"]), str(structure["choice"]))
    expect_paper_full = round(
        sum((g["to"] - g["from"] + 1) * g["per_score"] for g in structure["choice"]) +
        sum(s["full"] for s in structure["subjective"]), 1)
    check(f"模块满分按这份卷子算（{expect_paper_full}）",
          structure["module_full"]["total"] == expect_paper_full, str(structure["module_full"]))

    print("\n2.6) 用户端录成绩跟着试卷结构走（管理员改了立刻生效）")
    paper_schema = call("GET", f"/api/scores/schema?year=2009", key="", token=user_token)
    check("该年份的表单结构来自刚上传的试卷",
          paper_schema["source"] != "default" and paper_schema["paper_id"] == paper_id,
          f'{paper_schema["source"]} paper={paper_schema["paper_id"]}')
    check("模块满分与试卷结构一致", paper_schema["module_full"]["total"] == expect_paper_full,
          str(paper_schema["module_full"]))
    check("未登录读/改结构都是 401",
          status_of("GET", f"/api/papers/{paper_id}/structure", "") == 401
          and status_of("PUT", f"/api/papers/{paper_id}/structure", "") == 401)
    try:
        call("PUT", f"/api/papers/{paper_id}/structure", key="", token=user_token,
             payload={"choice": [{"subject": "ds", "from": 1, "to": 5, "per_score": 3}], "subjective": []})
        check("普通用户被拦在结构编辑外", False, "竟然改成功了")
    except urllib.error.HTTPError as exc:
        check("普通用户被拦在结构编辑外 403", exc.code == 403, str(exc.code))

    saved = call("PUT", f"/api/papers/{paper_id}/structure", payload={
        "choice": [{"subject": "ds", "from": 1, "to": 5, "per_score": 3}], "subjective": []})
    check("管理员保存结构（source=manual）",
          saved["source"] == "manual" and saved["module_full"]["ds"] == 15.0, str(saved["module_full"]))
    check("保存后用户端表单跟着变",
          call("GET", "/api/scores/schema?year=2009", key="", token=user_token)["module_full"]["total"] == 15.0)
    try:
        call("PUT", f"/api/papers/{paper_id}/structure", payload={
            "choice": [{"subject": "ds", "from": 1, "to": 5, "per_score": 3},
                       {"subject": "co", "from": 3, "to": 9, "per_score": 2}], "subjective": []})
        check("重叠题号被拦下", False, "竟然通过了")
    except urllib.error.HTTPError as exc:
        check("重叠题号被拦下 400", exc.code == 400, str(exc.code))

    rebuilt = call("POST", f"/api/papers/{paper_id}/structure/rebuild")
    check("按题目重建回到卷面结构", rebuilt["module_full"]["total"] == expect_paper_full,
          str(rebuilt["module_full"]))

    scored = call("POST", "/api/scores", key="", token=user_token, payload={
        "paper_year": 2009, "paper_id": paper_id, "practice_date": "2026-09-18",
        "choice": {g["key"]: g["count"] for g in paper_schema["choice_groups"]}, "subjective": []})
    check(f"按卷面结构录一条：满分 {expect_paper_full}",
          scored["total_score"] == expect_paper_full and scored["total_full"] == expect_paper_full,
          f'{scored["total_score"]} / {scored["total_full"]}')
    check("记录里存了试卷 id 与结构来源",
          scored["detail"]["paper_id"] == paper_id and scored["detail"]["schema_source"] == "auto",
          f'{scored["detail"].get("paper_id")} / {scored["detail"].get("schema_source")}')
    call("DELETE", f"/api/scores/{scored['id']}", key="", token=user_token)

    print("\n3) 用户端接口（要登录）")
    catalog = call("GET", "/api/catalog", key="", token=user_token)
    check("catalog 列出 2009 年", any(y["year"] == 2009 for y in catalog["years"]), str(catalog["years"]))
    public_qs = call("GET", "/api/questions?year=2009", key="", token=user_token)
    check("登录后能读题目列表", len(public_qs) >= 5, str(len(public_qs)))

    print("\n4) 图片接口")
    status, headers, body = call("GET", f"/api/papers/{paper_id}/pages/1", binary=True)
    check("页面图 image/png（管理接口）", status == 200 and headers.get("Content-Type", "").startswith("image/png"),
          f"{status} {headers.get('Content-Type')} {len(body)}B")
    status, headers, before = call("GET", questions[0]["image_urls"][0], binary=True, key="", token=user_token)
    check("登录后能取题目图（Cookie/Token 都行）", status == 200 and len(before) > 1000, f"{len(before)}B")

    print("\n4.5) Cookie 登录态（浏览器 <img src> 和 Word 下载链接只能靠它）")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_body = json.dumps({"username": username, "password": "secret123"}).encode("utf-8")
    opener.open(
        urllib.request.Request(
            BASE + "/api/auth/login", data=login_body,
            headers={"Content-Type": "application/json"}, method="POST",
        ),
        timeout=30,
    )
    names = {c.name for c in jar}
    check(f"登录后下发 Cookie {config_cookie_name()}", config_cookie_name() in names, str(names))
    with opener.open(BASE + questions[0]["image_urls"][0], timeout=60) as resp:
        body = resp.read()
    check("只带 Cookie（无 Authorization 头）就能取题目图", resp.status == 200 and len(body) > 1000, f"{len(body)}B")

    print("\n5) 人工校正：改边界 -> 自动重裁")
    q1 = questions[0]
    block = q1["bbox"]["blocks"][0]
    new_y1 = int(block["y1"]) - 30
    patched = call("PATCH", f"/api/questions/{q1['id']}",
                   payload={"block": {"page_no": block["page_no"], "y0": block["y0"], "y1": new_y1}})
    check(f"边界已更新 y1={new_y1}", patched["bbox"]["blocks"][0]["y1"] == new_y1,
          str(patched["bbox"]["blocks"][0]["y1"]))
    status, _h, after = call("GET", patched["image_urls"][0], binary=True, key="", token=user_token)
    check("重裁后图片变小", 1000 < len(after) < len(before), f"{len(before)} -> {len(after)} bytes")
    check("标记 source=manual", patched["source"] == "manual", patched["source"])

    print("\n6) 切分 / 合并")
    q4 = next(q for q in questions if q["question_no"] == 4)
    b4 = q4["bbox"]["blocks"][0]
    mid_y = int((b4["y0"] + b4["y1"]) / 2)
    split = call("POST", f"/api/papers/{paper_id}/questions/split",
                 payload={"page_no": b4["page_no"], "y": mid_y})
    check("切分产生新题", split["new_question"]["id"] > 0, f"id={split['new_question']['id']}")
    check("题数变 6", len(call("GET", f"/api/papers/{paper_id}")["questions"]) == 6)

    merged = call("POST", f"/api/papers/{paper_id}/questions/merge",
                  payload={"keep_id": q4["id"], "drop_id": split["new_question"]["id"]})
    check("合并回一块", len(merged["question"]["bbox"]["blocks"]) == 1,
          str(len(merged["question"]["bbox"]["blocks"])))
    detail = call("GET", f"/api/papers/{paper_id}")
    check("题数恢复 5", len(detail["questions"]) == 5, str(len(detail["questions"])))

    print("\n7) 导出 Word（要登录）")
    export = call("POST", "/api/export", key="", token=user_token, payload={
        "question_ids": [q["id"] for q in detail["questions"]],
        "with_caption": True, "note_lines": 1, "image_width_cm": 16,
    })
    check("文件名格式正确", bool(re.match(r"^408错题本_\d{4}-\d{2}-\d{2}_\d{4}\.docx$", export["filename"])),
          export["filename"])
    check("题目数 = 5", export["question_count"] == 5, str(export["question_count"]))
    status, _h, blob = call("GET", export["download_url"], binary=True, key="", token=user_token)
    check("登录后能下载 docx", status == 200 and len(blob) > 5000, f"{round(len(blob)/1024)} KB")

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
        media = [n for n in zf.namelist() if n.startswith("word/media/")]
    drawings = xml.count("<w:drawing>")
    check("含 6 张图", drawings == 6 and len(media) == 6, f"drawings={drawings} media={len(media)}")
    check("含 keepNext/keepLines", "w:keepNext" in xml and "w:keepLines" in xml)
    check("含标题与笔记留白", "年第" in xml)
    check("导出历史有记录", len(call("GET", "/api/exports", key="", token=user_token)) >= 1)

    print("\n7.5) 手机端下载：一次性票（外部浏览器没有登录 Cookie）")
    latest = call("GET", "/api/exports", key="", token=user_token)[0]
    ticket = call("POST", f"/api/exports/{latest['id']}/ticket", key="", token=user_token)
    check("登录态能换到下载票", "ticket=" in ticket["url"] and ticket["expires_in"] > 0, ticket["url"])
    status, _h, blob2 = call("GET", ticket["url"], binary=True, key="")  # 不带 token / 不带 Cookie
    check("拿着票、不带登录态也能下到 docx", status == 200 and len(blob2) > 5000, f"{round(len(blob2)/1024)} KB")
    try:
        call("GET", ticket["url"], binary=True, key="")
        check("票是一次性的（第二次就失效）", False, "居然还能下")
    except urllib.error.HTTPError as exc:
        check("票是一次性的（第二次就失效）", exc.code == 401, str(exc.code))
    try:
        call("GET", latest["download_url"], binary=True, key="")
        check("没有票、又没有登录态 -> 401", False, "居然能下")
    except urllib.error.HTTPError as exc:
        check("没有票、又没有登录态 -> 401", exc.code == 401, str(exc.code))

    print("\n8) 静态资源")
    status, _h, index = call("GET", "/", binary=True, key="")
    check("用户端 H5 可访问", status == 200 and "408 错题本" in index.decode("utf-8"))
    index_html = index.decode("utf-8")
    check("首页给 js/css 打了版本戳（文件一改 URL 就变，旧缓存必然失效）",
          "scores.js?v=" in index_html and "core.js?v=" in index_html and "style.css?v=" in index_html,
          " ".join(part for part in index_html.split() if "?v=" in part)[:120])
    status, _h, js = call("GET", "/app.js", binary=True, key="")
    check("app.js 可访问", status == 200 and len(js) > 3000, f"{len(js)}B")
    status, headers, scores_js = call("GET", "/scores.js", binary=True, key="")
    check("scores.js 是最新内容（带客观/主观分块）",
          "客观题".encode("utf-8") in scores_js, f"{len(scores_js)}B")
    check("静态资源强制回源校验（改了前端刷新就生效）",
          "no-cache" in (headers.get("Cache-Control") or ""), str(headers.get("Cache-Control")))

    print("\n9) 清理测试数据")
    call("DELETE", f"/api/papers/{paper_id}")
    for item in call("GET", "/api/exports", key="", token=user_token):
        call("DELETE", f"/api/exports/{item['id']}", key="", token=user_token)
    call("DELETE", f"/api/admin/users/{user_id}")
    remain = call("GET", "/api/papers")
    check("真题已清理", all(p["id"] != paper_id for p in remain), f"剩余 {len(remain)} 份")
    check("测试用户已清理", all(u["id"] != user_id for u in call("GET", "/api/admin/users")))

    print("\n全部通过" if ok else "\n存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
