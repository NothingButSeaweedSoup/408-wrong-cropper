"""`python -m backend` 启动服务（host/port 统一从 backend/config.py 读）。

等价于：
    .venv\\Scripts\\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 18100
加 --reload 可开热重载。
"""

from __future__ import annotations

import sys

import uvicorn

from . import config

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        reload="--reload" in sys.argv,
    )
