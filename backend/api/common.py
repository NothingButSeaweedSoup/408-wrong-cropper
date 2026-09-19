"""API 公共工具：路径还原、行 -> 响应体。"""

from __future__ import annotations

from pathlib import Path

from .. import config, db
from ..services import layout


def abs_path(rel: str | Path) -> Path:
    path = Path(rel)
    return path if path.is_absolute() else config.ROOT_DIR / path


def page_out(row) -> dict:
    return {
        "page_no": int(row["page_no"]),
        "width": int(row["width"]),
        "height": int(row["height"]),
        "url": f"/api/papers/{int(row['paper_id'])}/pages/{int(row['page_no'])}",
    }


def question_out(row, year: int | None = None) -> dict:
    data = db.question_out(row)
    qid = int(data["id"])
    data["image_urls"] = [f"/api/questions/{qid}/images/{i}" for i in range(len(data["image_paths"]))]
    data["year"] = year
    data["height_cm"] = round(
        sum(
            layout.piece_height_cm(data, int(b["y1"]) - int(b["y0"]), config.IMAGE_WIDTH_CM)
            for b in (data["bbox"].get("blocks") or [])
        ),
        2,
    )
    data.pop("bbox_json", None)
    data.pop("image_paths", None)
    return data


def paper_out(row, question_count: int = 0) -> dict:
    return {
        "id": int(row["id"]),
        "year": int(row["year"]),
        "title": row["title"] or "",
        "status": row["status"],
        "progress": int(row["progress"]),
        "message": row["message"] or "",
        "page_count": int(row["page_count"]),
        "dpi": float(row["dpi"]),
        "created_at": row["created_at"],
        "question_count": question_count,
    }
