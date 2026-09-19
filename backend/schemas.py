"""API 请求/响应模型。

只放真正用到的：请求体（校正 / 切分 / 合并 / 导出）+ 导出响应。
列表类响应结构比较动态（要拼 image_urls、height_cm 等），由 api/common.py 组装，
不在 pydantic 里重复声明一份，避免两处失真。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QuestionPatch(BaseModel):
    """人工校正：改题号 / 科目 / 题型，或直接改某一块的上下边界（页面像素坐标）。"""

    question_no: int | None = None
    subject: Literal["ds", "co", "os", "cn"] | None = None
    type: Literal["choice", "subjective"] | None = None
    score: float | None = None
    # 只改指定页面的边界，例如 {"page_no": 3, "y0": 120, "y1": 980}
    block: dict | None = None


class SplitRequest(BaseModel):
    page_no: int
    y: int  # 在页面像素坐标 y 处切开，上半留在原题，下半成为新题


class MergeRequest(BaseModel):
    keep_id: int
    drop_id: int


class ExportRequest(BaseModel):
    question_ids: list[int] = Field(default_factory=list)
    paper_ids: list[int] = Field(default_factory=list)
    with_caption: bool = False
    note_lines: int = 0
    image_width_cm: float | None = None


class ExportOut(BaseModel):
    filename: str
    download_url: str
    question_count: int
    page_count_estimate: int = 0
