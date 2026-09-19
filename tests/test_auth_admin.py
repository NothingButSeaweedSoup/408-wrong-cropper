"""管理后台鉴权测试：管理员账号（.env 指定）+ 兜底密钥。

直接在进程内按 ASGI 协议驱动整个 app（含鉴权中间件），不起端口、不依赖 httpx：
覆盖「未登录 401 / 不是管理员 403 / 管理员账号 200 / 兜底密钥 200 / 后台改标记后立即生效」。

跑法：.venv\\Scripts\\python.exe tests\\test_auth_admin.py
数据写在 data/_selftest_auth/，不污染正式库。
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DATA = ROOT / "data" / "_selftest_auth"
if TEST_DATA.exists():
    shutil.rmtree(TEST_DATA, ignore_errors=True)

# 必须在 import backend 之前设置：config 在导入时读环境变量
os.environ["ZC_DATA_DIR"] = str(TEST_DATA)
os.environ["ZC_ADMIN_KEY"] = "test-admin-key"
os.environ["ZC_ADMIN_USERS"] = "boss, 中文管理员"  # 顺便验证逗号 + 空格 + 中文

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from backend import config  # noqa: E402
from backend.main import app  # noqa: E402

KEY = {"x-admin-key": "test-admin-key"}
ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and bool(condition)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


async def call(method: str, path: str, *, headers: dict | None = None, body: dict | None = None):
    """返回 (status, json)。按 ASGI 协议直接调用 app，中间件/路由都是真的。"""
    raw = b""
    head = dict(headers or {})
    if body is not None:
        raw = jsonlib.dumps(body, ensure_ascii=False).encode("utf-8")
        head["content-type"] = "application/json"
    path_only, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path_only,
        "raw_path": path_only.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(k.lower().encode(), str(v).encode()) for k, v in head.items()],
        "client": ("127.0.0.1", 45678),
        "server": ("127.0.0.1", config.SERVER_PORT),
    }
    sent: list[dict] = []
    delivered = {"body": False}
    idle = asyncio.Event()  # 永不 set：模拟"客户端一直在等响应"

    async def receive():
        # 第一次给请求体；之后**阻塞**（真实 ASGI 服务器也是阻塞等客户端），
        # 响应发完时 starlette 会取消这个等待。直接返回 http.disconnect 会让响应体被掐掉。
        if not delivered["body"]:
            delivered["body"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await idle.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    try:
        return status, jsonlib.loads(payload)
    except ValueError:
        return status, {"raw": payload[:200].decode("utf-8", "replace")}


def bearer(token: str) -> dict:
    return {"authorization": f"Bearer {token}"}


async def scenario() -> None:
    async def register(username: str, password: str = "secret123") -> dict:
        _, data = await call("POST", "/api/auth/register", body={"username": username, "password": password})
        return data

    print("\n1) 未登录 / 非管理员 / 管理员")
    status, _ = await call("GET", "/api/papers")
    check("什么都不带 -> 401", status == 401, str(status))
    status, _ = await call("GET", "/api/papers", headers=KEY)
    check("兜底密钥 -> 200", status == 200, str(status))
    _, info = await call("GET", "/api/admin/verify", headers=KEY)
    check("verify 认出是密钥登录", info.get("via") == "key", str(info.get("via")))

    boss = await register("boss", "boss-pass-123")
    check("boss 注册后自动是管理员（名单里）", boss["user"]["is_admin"] is True, str(boss["user"]))
    status, _ = await call("GET", "/api/papers", headers=bearer(boss["token"]))
    check("boss 的 token 能进管理接口", status == 200, str(status))
    _, info = await call("GET", "/api/admin/verify", headers=bearer(boss["token"]))
    check("verify 认出是账号登录", info.get("via") == "user" and info["user"]["username"] == "boss", str(info))

    normal = await register("normal", "normal-pass-123")
    check("普通用户不是管理员", normal["user"]["is_admin"] is False, str(normal["user"]))
    status, data = await call("GET", "/api/papers", headers=bearer(normal["token"]))
    check("普通用户进管理接口 -> 403（不是 401）", status == 403, str(status))
    check("403 提示里说明怎么加管理员", "ZC_ADMIN_USERS" in data.get("detail", ""), data.get("detail", ""))

    print("\n2) 用户端功能不受影响")
    status, _ = await call("GET", "/api/questions", headers=bearer(normal["token"]))
    check("普通用户能读题目库", status == 200, str(status))
    status, _ = await call("GET", "/api/questions")
    check("未登录读题目库 -> 401", status == 401, str(status))
    status, _ = await call("GET", "/api/questions/1/images/0")
    check("未登录取题目图 -> 401", status == 401, str(status))
    status, _ = await call("PATCH", "/api/questions/1", headers=bearer(normal["token"]), body={"question_no": 2})
    check("普通用户不能改题（改题属管理操作）-> 403", status == 403, str(status))
    status, _ = await call("DELETE", "/api/questions/1", headers=bearer(normal["token"]))
    check("普通用户不能删题 -> 403", status == 403, str(status))
    status, _ = await call("GET", "/api/catalog", headers=bearer(normal["token"]))
    check("登录后能读 catalog", status == 200, str(status))
    status, _ = await call("POST", "/api/export", headers=bearer(normal["token"]), body={"question_ids": []})
    check("登录后能调导出（空选择报 400 而不是 401）", status == 400, str(status))
    # 手机端下载：外部浏览器没有登录 Cookie，靠一次性票；票本身由接口校验
    status, data = await call("GET", "/api/exports/999/download")
    check("导出下载不带票 -> 中间件拦下 401", status == 401 and "请先登录" in str(data.get("detail", "")),
          f'{status} {data.get("detail", "")}')
    status, data = await call("GET", "/api/exports/999/download?ticket=bogus")
    check("带（假）票 -> 放行进接口，由接口判失效 401",
          status == 401 and "失效" in str(data.get("detail", "")), f'{status} {data.get("detail", "")}')
    status, _ = await call("POST", "/api/exports/999/ticket", headers=bearer(normal["token"]))
    check("换票接口要登录（记录不存在时 404，说明鉴权已过）", status == 404, str(status))
    status, _ = await call("POST", "/api/exports/999/ticket")
    check("未登录换不了票 -> 401", status == 401, str(status))
    status, score = await call(
        "POST", "/api/scores", headers=bearer(normal["token"]),
        body={"paper_year": 2009, "practice_date": "2026-09-19",
              "choice": {"ds": 11, "co": 11, "os": 10, "cn": 8}, "subjective": []},
    )
    check("普通用户能录成绩", status == 200 and score["total_score"] == 80.0, str(score.get("total_score")))
    status, _ = await call("GET", "/api/scores")
    check("未登录读成绩 -> 401", status == 401, str(status))
    status, _ = await call("GET", "/api/scores", headers=KEY)
    check("管理员密钥也读不了个人成绩（只认 token）", status == 401, str(status))

    print("\n3) 后台直接把人设成管理员")
    _, users = await call("GET", "/api/admin/users", headers=KEY)
    flags = {u["username"]: u["is_admin"] for u in users}
    check("用户列表带 is_admin", flags == {"boss": True, "normal": False}, str(flags))
    normal_id = next(u["id"] for u in users if u["username"] == "normal")
    boss_id = next(u["id"] for u in users if u["username"] == "boss")
    status, data = await call("PATCH", f"/api/admin/users/{normal_id}", headers=KEY, body={"is_admin": True})
    check("设为管理员成功", status == 200 and data["is_admin"] is True, str(data))
    status, _ = await call("GET", "/api/papers", headers=bearer(normal["token"]))
    check("normal 立刻能进管理接口", status == 200, str(status))

    print("\n4) 自我保护")
    status, data = await call("PATCH", f"/api/admin/users/{boss_id}", headers=bearer(boss["token"]),
                              body={"is_admin": False})
    check("管理员不能取消自己 -> 400", status == 400, data.get("detail", ""))
    status, data = await call("PATCH", f"/api/admin/users/{boss_id}", headers=KEY, body={"is_admin": False})
    check("用兜底密钥可以取消（救急路径）", status == 200 and data["is_admin"] is False, str(data))
    status, _ = await call("PATCH", f"/api/admin/users/{normal_id}", headers=bearer(boss["token"]),
                           body={"is_admin": True})
    check("取消后 boss 也进不去了 -> 403", status == 403, str(status))

    print("\n5) 新建用户时可直接给管理员")
    status, data = await call("POST", "/api/admin/users", headers=KEY,
                              body={"username": "created", "password": "created-pass", "is_admin": True})
    check("管理员代建管理员账号", status == 200 and data["is_admin"] is True, str(data))
    _, login = await call("POST", "/api/auth/login", body={"username": "created", "password": "created-pass"})
    status, _ = await call("GET", "/api/papers", headers=bearer(login["token"]))
    check("该账号能登录并进后台", status == 200, str(status))


def main() -> int:
    print("管理员名单解析")
    check("ZC_ADMIN_USERS 解析出 2 个名字（去空格）", config.ADMIN_USERS == {"boss", "中文管理员"},
          str(sorted(config.ADMIN_USERS)))
    check("前后空格容错", config.is_admin_username(" boss ") and not config.is_admin_username("bo"))

    # 手动跑一次 lifespan（init_db / 同步管理员名单），平时由 uvicorn 触发
    async def run():
        async with app.router.lifespan_context(app):
            await scenario()

    asyncio.run(run())
    print("\n全部通过" if ok else "\n存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
