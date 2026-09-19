"""得分记录：按"题组答对数 + 综合题逐题得分"算出模块分/总分，以及趋势聚合。

录入口径（对应 408 卷面）：
- 选择题按 4 个题组填**答对个数**，每组每题 2 分（结构来自 config.CHOICE_GROUPS）；
- 综合题 41~47 逐题填**得分**与**满分**（各年分布不同，表单里可改）；
- 模块分 = 该模块选择题得分 + 该模块综合题得分，总分 = 四模块之和。

存库时把录入明细（含当时用的满分）一起写进 detail_json，
这样以后有人改了默认满分，老记录的分数也不会被"重新解释"。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from .. import config, db

X_AXES = ("practice_date", "paper_year")
AGGREGATES = ("latest", "avg", "max")

# 当前水平估计：最近三次的权重（最近 → 最远）
ESTIMATE_WEIGHTS = (0.5, 0.35, 0.15)
MODULES = ("ds", "co", "os", "cn")


class ScoreError(ValueError):
    """录入数据不合法，消息可直接给前端看。"""


def _to_float(value, label: str, *, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ScoreError(f"{label} 要是数字") from None
    if number < low - 1e-6 or number > high + 1e-6:
        raise ScoreError(f"{label} 应在 {low:g}~{high:g} 之间")
    return round(number, 1)


def compute(payload: dict) -> dict:
    """把录入数据算成模块分/总分，返回可直接入库的结构。"""
    schema = config.score_form_schema(payload.get("paper_year"))
    choice_counts = payload.get("choice") or {}
    subjective_scores = {int(item["qno"]): item for item in (payload.get("subjective") or [])}

    module_score = {key: 0.0 for key in ("ds", "co", "os", "cn")}
    details: dict = {"choice": [], "subjective": []}

    for group in schema["choice_groups"]:
        subject = group["subject"]
        raw = choice_counts.get(subject, 0)
        try:
            correct = int(raw)
        except (TypeError, ValueError):
            raise ScoreError(f"{group['name']}选择题答对数要是整数") from None
        if correct < 0 or correct > group["count"]:
            raise ScoreError(f"{group['name']}选择题答对数应在 0~{group['count']} 之间")
        score = round(correct * group["per_score"], 1)
        module_score[subject] += score
        details["choice"].append(
            {
                "subject": subject,
                "name": group["name"],
                "from": group["from"],
                "to": group["to"],
                "count": group["count"],
                "correct": correct,
                "per_score": group["per_score"],
                "score": score,
                "full": group["full"],
            }
        )

    for item in schema["subjective"]:
        qno = int(item["qno"])
        entry = subjective_scores.get(qno) or {}
        full = _to_float(entry.get("full", item["full"]), f"第 {qno} 题满分", low=0, high=30)
        score = _to_float(entry.get("score", 0), f"第 {qno} 题得分", low=0, high=max(full, 0.1))
        if score > full + 1e-6:
            raise ScoreError(f"第 {qno} 题得分不能超过满分 {full:g}")
        module_score[item["subject"]] += score
        details["subjective"].append(
            {"qno": qno, "subject": item["subject"], "name": item["name"], "score": score, "full": full}
        )

    module_score = {key: round(value, 1) for key, value in module_score.items()}
    total = round(sum(module_score.values()), 1)
    return {
        "paper_year": int(payload.get("paper_year") or 0),
        "practice_date": str(payload.get("practice_date") or date.today().isoformat()),
        "total_score": total,
        "ds_score": module_score["ds"],
        "co_score": module_score["co"],
        "os_score": module_score["os"],
        "cn_score": module_score["cn"],
        "detail": {
            "input": {
                "choice": {g["subject"]: g["correct"] for g in details["choice"]},
                "subjective": {str(s["qno"]): {"score": s["score"], "full": s["full"]} for s in details["subjective"]},
                "note": str(payload.get("note") or "")[:200],
            },
            "module_full": config.MODULE_FULL_SCORE,
            "breakdown": details,
        },
    }


def validate_meta(payload: dict) -> tuple[int, str]:
    try:
        year = int(payload.get("paper_year") or 0)
    except (TypeError, ValueError):
        raise ScoreError("真题年份要是数字") from None
    if year < 1990 or year > 2100:
        raise ScoreError("真题年份看起来不对")
    practice_date = str(payload.get("practice_date") or date.today().isoformat())[:10]
    try:
        date.fromisoformat(practice_date)
    except ValueError:
        raise ScoreError("做题日期格式应为 YYYY-MM-DD") from None
    return year, practice_date


def save_record(conn: sqlite3.Connection, user_id: int, payload: dict, record_id: int | None = None) -> sqlite3.Row:
    year, practice_date = validate_meta(payload)
    computed = compute({**payload, "paper_year": year, "practice_date": practice_date})
    note = str(payload.get("note") or "")[:200]
    detail = computed["detail"]
    detail["input"]["note"] = note

    if record_id is None:
        cur = conn.execute(
            """INSERT INTO exam_records
               (user_id, paper_year, practice_date, total_score, ds_score, co_score, os_score, cn_score,
                detail_json, note, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, year, practice_date, computed["total_score"], computed["ds_score"],
                computed["co_score"], computed["os_score"], computed["cn_score"],
                json.dumps(detail, ensure_ascii=False), note, db.now(),
            ),
        )
        record_id = int(cur.lastrowid)
    else:
        owned = conn.execute(
            "SELECT id FROM exam_records WHERE id=? AND user_id=?", (record_id, user_id)
        ).fetchone()
        if owned is None:
            raise ScoreError("记录不存在")
        conn.execute(
            """UPDATE exam_records SET paper_year=?, practice_date=?, total_score=?, ds_score=?,
               co_score=?, os_score=?, cn_score=?, detail_json=?, note=? WHERE id=?""",
            (
                year, practice_date, computed["total_score"], computed["ds_score"], computed["co_score"],
                computed["os_score"], computed["cn_score"],
                json.dumps(detail, ensure_ascii=False), note, record_id,
            ),
        )
    return conn.execute("SELECT * FROM exam_records WHERE id=?", (record_id,)).fetchone()


def record_out(row: sqlite3.Row | dict) -> dict:
    data = db.row_to_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    detail = db.load_json(data.pop("detail_json", "{}"), {})
    full = config.MODULE_FULL_SCORE
    scores = {key: float(data[f"{key}_score"]) for key in MODULES}
    return {
        **data,
        "total_score": float(data["total_score"]),
        **scores,
        "detail": detail,
        # 模块得分率（0~100），趋势图直接可用
        "rates": {key: round(scores[key] / full[key] * 100, 1) for key in MODULES},
        "total_full": full["total"],
        "total_rate": round(float(data["total_score"]) / full["total"] * 100, 1),
    }


def list_records(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM exam_records WHERE user_id=? ORDER BY practice_date DESC, created_at DESC, id DESC",
        (user_id,),
    ).fetchall()
    return [record_out(r) for r in rows]


def _record_module_full(detail: dict) -> dict[str, float]:
    """算这条记录**当时**各模块的满分。

    各年份综合题分布不同（42 题某年 13 分、某年 15 分），所以模块得分率必须按
    记录自己的满分算，不能拿固定的 45/45/35/25 去除，否则会引入偏差。
    老记录没有明细时退回配置里的固定满分。
    """
    fulls = {key: 0.0 for key in MODULES}
    breakdown = (detail or {}).get("breakdown") or {}
    for group in breakdown.get("choice") or []:
        if group.get("subject") in fulls:
            fulls[group["subject"]] += float(group.get("full") or 0)
    for item in breakdown.get("subjective") or []:
        if item.get("subject") in fulls:
            fulls[item["subject"]] += float(item.get("full") or 0)
    if not any(fulls.values()):
        return {key: float(config.MODULE_FULL_SCORE[key]) for key in MODULES}
    return fulls


def estimate(conn: sqlite3.Connection, user_id: int, limit: int = 3) -> dict:
    """当前水平估计：最近 N 次成绩的加权平均。

    - 权重 50% / 35% / 15%（最近 → 最远）；不足 3 次时**按已有次数归一化**，
      例如 2 次是 50/85≈58.8% 与 35/85≈41.2%，1 次就是 100%；
    - 总分：用原始分加权（满分固定 150）；
    - 模块：先按各条记录当时的满分算得分率，再加权，避免满分不同导致偏差。
    """
    rows = conn.execute(
        """SELECT * FROM exam_records WHERE user_id=?
           ORDER BY practice_date DESC, created_at DESC, id DESC LIMIT ?""",
        (user_id, max(1, limit)),
    ).fetchall()
    total_full = float(config.MODULE_FULL_SCORE["total"])
    if not rows:
        return {
            "count": 0,
            "weights": [],
            "base_weights": [],
            "modules": {key: None for key in MODULES},
            "total": None,
            "total_full": total_full,
            "total_rate": None,
            "samples": [],
            "message": "还没有成绩记录",
        }

    picked = rows[: len(ESTIMATE_WEIGHTS)]
    base = list(ESTIMATE_WEIGHTS[: len(picked)])
    base_sum = sum(base)
    weights = [round(w / base_sum, 4) for w in base]  # 不足 3 次时归一化

    module_rates = {key: 0.0 for key in MODULES}
    total = 0.0
    samples: list[dict] = []
    for row, weight in zip(picked, weights):
        data = record_out(row)
        fulls = _record_module_full(data["detail"])
        sample_rates = {
            key: round(float(data[f"{key}_score"]) / fulls[key] * 100, 1) if fulls.get(key) else 0.0
            for key in MODULES
        }
        for key in MODULES:
            module_rates[key] += sample_rates[key] * weight
        total += float(data["total_score"]) * weight
        samples.append(
            {
                "record_id": int(row["id"]),
                "practice_date": row["practice_date"],
                "paper_year": int(row["paper_year"]),
                "total_score": float(data["total_score"]),
                "weight": weight,
                "module_full": {key: round(fulls[key], 1) for key in MODULES},
                "rates": sample_rates,
            }
        )

    return {
        "count": len(picked),
        "weights": weights,
        "base_weights": base,
        "modules": {key: round(module_rates[key], 1) for key in MODULES},
        "total": round(total, 1),
        "total_full": total_full,
        "total_rate": round(total / total_full * 100, 1),
        "samples": samples,
        "message": "" if len(picked) == 3 else f"只有 {len(picked)} 次记录，权重已按比例归一化",
    }


def trend(conn: sqlite3.Connection, user_id: int, x_axis: str = "practice_date", aggregate: str = "latest") -> dict:
    x_axis = x_axis if x_axis in X_AXES else "practice_date"
    aggregate = aggregate if aggregate in AGGREGATES else "latest"
    rows = conn.execute(
        "SELECT * FROM exam_records WHERE user_id=? ORDER BY practice_date ASC, created_at ASC, id ASC",
        (user_id,),
    ).fetchall()
    full = config.MODULE_FULL_SCORE
    labels: list[str] = []
    points: list[dict] = []

    if x_axis == "paper_year":
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(int(row["paper_year"]), []).append(row)
        for year in sorted(grouped):
            bucket = grouped[year]
            if aggregate == "avg":
                values = {
                    key: round(sum(float(r[f"{key}_score"]) for r in bucket) / len(bucket), 1)
                    for key in ("ds", "co", "os", "cn")
                }
                total = round(sum(values.values()), 1)
                labels.append(str(year))
                points.append({"label": str(year), "year": year, "count": len(bucket), **values, "total_score": total})
            else:
                chosen = bucket[-1] if aggregate == "latest" else max(bucket, key=lambda r: float(r["total_score"]))
                labels.append(str(year))
                points.append(
                    {
                        "label": str(year),
                        "year": year,
                        "count": len(bucket),
                        "practice_date": chosen["practice_date"],
                        "record_id": int(chosen["id"]),
                        **{key: float(chosen[f"{key}_score"]) for key in ("ds", "co", "os", "cn")},
                        "total_score": float(chosen["total_score"]),
                    }
                )
    else:
        for row in rows:
            labels.append(str(row["practice_date"]))
            points.append(
                {
                    "label": str(row["practice_date"]),
                    "year": int(row["paper_year"]),
                    "count": 1,
                    "practice_date": row["practice_date"],
                    "record_id": int(row["id"]),
                    **{key: float(row[f"{key}_score"]) for key in ("ds", "co", "os", "cn")},
                    "total_score": float(row["total_score"]),
                }
            )

    series = {
        key: [round(p[key] / full[key] * 100, 1) for p in points] for key in ("ds", "co", "os", "cn")
    }
    series["total"] = [p["total_score"] for p in points]
    return {
        "x_axis": x_axis,
        "aggregate": aggregate,
        "labels": labels,
        "series": series,
        "points": points,
        "module_full": full,
        "empty": not points,
        # 曲线图上方那个小表格：始终按**做题时间**取最近 3 次，与 X 轴怎么切无关
        "estimate": estimate(conn, user_id),
    }
