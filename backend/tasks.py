"""极简后台任务：单进程线程池。

刻意不用 Celery/RQ：单管理员、单机跑，一次导入几十页，串行执行足够，
省掉 broker + worker + 依赖（AGENTS.md 里 Celery 标注为"可选"）。
"""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zc-pipeline")


def submit(fn: Callable, *args, **kwargs) -> None:
    """把任务丢进队列，立即返回。"""
    _executor.submit(_run, fn, args, kwargs)


def _run(fn: Callable, args: tuple, kwargs: dict) -> None:
    try:
        fn(*args, **kwargs)
    except Exception:  # 任务内部已写库，这里只兜底打印，避免线程静默死掉
        traceback.print_exc()
