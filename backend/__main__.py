"""`python -m backend` 启动服务（host/port 统一从 backend/config.py 读）。

用法：
    python -m backend              启动服务
    python -m backend --reload     启动并开热重载
    python -m backend --print-ip   只打印本机可用访问地址（手机/安卓 App 要填的那个 IP）
"""

from __future__ import annotations

import sys

import uvicorn

from . import config


def print_access_urls() -> None:
    port = config.SERVER_PORT
    print(f"服务端口 {port}，可用访问地址：")
    print(f"  http://127.0.0.1:{port}/      本机（只在这台电脑上用）")
    ips = config.local_ips()
    if not ips:
        print("  （没查到局域网 IP：检查网络连接，或是否只连了虚拟网卡）")
        return
    print(f"  http://{ips[0]}:{port}/      ← 手机 / 平板 / 安卓 App 填这个（默认网卡）")
    for ip in ips[1:]:
        print(f"  http://{ip}:{port}/      （其它网卡，多半是 Hyper-V/VirtualBox/WSL 虚拟网卡，一般不用）")


if __name__ == "__main__":
    if "--print-ip" in sys.argv:
        print_access_urls()
        raise SystemExit(0)
    uvicorn.run(
        "backend.main:app",
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        reload="--reload" in sys.argv,
    )
