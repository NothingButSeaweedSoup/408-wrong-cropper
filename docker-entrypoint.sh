#!/bin/sh
# 容器入口：修好数据目录的属主，然后降权到非 root 跑应用。
#
# 为什么需要它：数据目录是 bind mount 挂进来的（compose 里 ./data:/data），
# 首次 `docker compose up` 时 Docker 会在宿主机上以 root 建这个目录，
# 容器里的非 root 用户（uid 10001 zc）就写不了 → 启动直接
# `PermissionError: [Errno 13] Permission denied: '/data/uploads'`。
# 以 root 起 → 把 /data 交给 10001 → 立刻 setpriv 降权，应用本身仍然是非 root 进程。
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/uploads /data/pages /data/crops /data/exports

    # 只有确实不可写时才递归改属主：换机器/首次启动才走这一步，
    # 否则每次重启都要遍历几万张页面图和裁图，白等。
    if ! setpriv --reuid=10001 --regid=10001 --clear-groups test -w /data; then
        echo "[entrypoint] /data 对 uid 10001 不可写，正在修正属主（首次启动或换机器时会这样）..."
        chown -R 10001:10001 /data || echo "[entrypoint] chown 失败（只读挂载？），继续尝试启动"
    fi

    exec setpriv --reuid=10001 --regid=10001 --clear-groups "$@"
fi

exec "$@"
