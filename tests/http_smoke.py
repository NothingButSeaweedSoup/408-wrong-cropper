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
    check("表单结构：4 组选择题 + 7 道综合题",
          len(schema["choice_groups"]) == 4 and len(schema["subjective"]) == 7,
          f'{len(schema["choice_groups"])}/{len(schema["subjective"])}')
    full = {"paper_year": 2009, "practice_date": "2026-09-17",
            "choice": {"ds": 11, "co": 11, "os": 10, "cn": 8},
            "subjective": [{"qno": s["qno"], "score": s["full"], "full": s["full"]} for s in schema["subjective"]],
            "note": "冒烟测试"}
    perfect = call("POST", "/api/scores", key="", token=user_token, payload=full)
    check("全对记录总分 150", perfect["total_score"] == 150, str(perfect["total_score"]))
    partial = call("POST", "/api/scores", key="", token=user_token, payload={
        "paper_year": 2010, "practice_date": "2026-09-25",
        "choice": {"ds": 9, "co": 8, "os": 7, "cn": 6},
        "subjective": [{"qno": s["qno"], "score": round(s["full"] / 2, 1), "full": s["full"]} for s in schema["subjective"]],
    })
    check("半对记录分数合理", 0 < partial["total_score"] < 150, str(partial["total_score"]))

    records = call("GET", "/api/scores", key="", token=user_token)
    check("列表 2 条且按时间倒序", len(records) == 2 and records[0]["practice_date"] == "2026-09-25",
          str([r["practice_date"] for r in records]))
    check("带模块得分率", records[0]["rates"]["ds"] > 0 and records[0]["total_full"] == 150)

    trend = call("GET", "/api/scores/trend?x_axis=practice_date", key="", token=user_token)
    check("时间轴 2 个点", len(trend["labels"]) == 2, str(trend["labels"]))
    check("模块百分比 + 总分绝对分", trend["series"]["ds"][0] == 100.0 and trend["series"]["total"][0] == 150)
    year_trend = call("GET", "/api/scores/trend?x_axis=paper_year&aggregate=latest", key="", token=user_token)
    check("年份轴按年份排序", year_trend["labels"] == ["2009", "2010"], str(year_trend["labels"]))

    estimate = trend["estimate"]
    check("趋势响应里带「当前水平估计」", estimate["count"] == 2, str(estimate["count"]))
    check("不足 3 次时权重归一化（50/85、35/85）",
          abs(estimate["weights"][0] - 50 / 85) < 1e-4 and abs(estimate["weights"][1] - 35 / 85) < 1e-4,
          str(estimate["weights"]))
    check("估计总分介于两次成绩之间", 95 < estimate["total"] < 150, str(estimate["total"]))
    check("估计里模块是得分率、总分是原始分",
          all(0 <= v <= 100 for v in estimate["modules"].values()) and estimate["total_full"] == 150,
          f'{estimate["modules"]} total={estimate["total"]}')
    check("估计样本按时间倒序", [s["practice_date"] for s in estimate["samples"]] == ["2026-09-25", "2026-09-17"],
          str([s["practice_date"] for s in estimate["samples"]]))

    edited = call("PUT", f"/api/scores/{partial['id']}", key="", token=user_token, payload={
        **full, "practice_date": "2026-09-26", "note": "改过"})
    check("编辑后重算并更新日期", edited["practice_date"] == "2026-09-26" and edited["total_score"] == 150,
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

    print("\n8) 静态资源")
    status, _h, index = call("GET", "/", binary=True, key="")
    check("用户端 H5 可访问", status == 200 and "408 错题本" in index.decode("utf-8"))
    status, _h, js = call("GET", "/app.js", binary=True, key="")
    check("app.js 可访问", status == 200 and len(js) > 3000, f"{len(js)}B")

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
