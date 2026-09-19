"""得分记录：按"题组答对数 + 综合题逐题得分"算出模块分/总分，以及趋势聚合。

录入口径**不写死**：表单结构来自那份试卷自己的**卷面结构**（`services/paper_structure.py`，
存在 `papers.structure_json`），因为 408 各年分布不一样——数据结构选择题某些年 10 题、
某些年 11 题；综合题 41~47 的分值年年不同。没导入过该年份的试卷时退回
`config.default_score_schema()`（`source == "default"`，前端会提示）。

- 选择题按题组填**答对个数**，每题分值取结构里的 `per_score`；
- 综合题逐题填**得分**与**满分**（满分默认取结构，用户可改，用于纠 OCR 读错的情况）；
- 模块分 = 该模块选择题得分 + 该模块综合题得分，总分 = 四模块之和。

存库时把录入明细（含**当时**的满分）一起写进 detail_json，
这样以后有人改了试卷结构，老记录的分数也不会被"重新解释"。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from .. import config, db
from . import paper_structure

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


def compute(payload: dict, schema: dict | None = None) -> dict:
    """把录入数据算成模块分/总分，返回可直接入库的结构。

    `schema` 由调用方从试卷结构解析（`paper_structure.resolve_schema`）；不传就用默认结构，
    方便测试与「该年份还没导入试卷」的兜底。
    """
    if schema is None:
        schema = config.default_score_schema(payload.get("paper_year"))
    choice_counts = payload.get("choice") or {}
    subjective_scores = {int(item["qno"]): item for item in (payload.get("subjective") or [])}

    module_score = {key: 0.0 for key in MODULES}
    details: dict = {"choice": [], "subjective": []}

    # 同一科目可能有多组选择题（某年 DS 1-5 每题 2 分、6-10 每题 3 分），
    # 所以录入按**分组 key**（`g起始题号`）来；该科目只有一组时也认科目名（老记录/老前端）。
    groups_per_subject: dict[str, int] = {}
    for group in schema["choice_groups"]:
        groups_per_subject[group["subject"]] = groups_per_subject.get(group["subject"], 0) + 1

    for group in schema["choice_groups"]:
        subject = group["subject"]
        key = str(group.get("key") or f"g{group['from']}")
        if key in choice_counts:
            raw = choice_counts[key]
        elif groups_per_subject.get(subject) == 1:
            raw = choice_counts.get(subject, 0)
        else:
            raw = 0
        try:
            correct = int(raw)
        except (TypeError, ValueError):
            raise ScoreError(f"{group['name']}选择题答对数要是整数") from None
        if correct < 0 or correct > group["count"]:
            raise ScoreError(f"{group['name']} {group['from']}-{group['to']} 选择题答对数应在 0~{group['count']} 之间")
        score = round(correct * group["per_score"], 1)
        module_score[subject] += score
        details["choice"].append(
            {
                "key": key,
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
        full = _to_float(entry.get("full", item["full"]), f"第 {qno} 题满分", low=0, high=50)
        score = _to_float(entry.get("score", 0), f"第 {qno} 题得分", low=0, high=max(full, 0.1))
        if score > full + 1e-6:
            raise ScoreError(f"第 {qno} 题得分不能超过满分 {full:g}")
        module_score[item["subject"]] += score
        details["subjective"].append(
            {
                "qno": qno,
                "subject": item["subject"],
                "name": item["name"],
                "score": score,
                "full": full,
                "schema_full": float(item["full"]),  # 结构里的默认满分，表单回填用
            }
        )

    module_score = {key: round(value, 1) for key, value in module_score.items()}
    total = round(sum(module_score.values()), 1)
    # 录入原样存两份 key：分组 key（多组时必需）+ 科目名（该科目只有一组时，兼容老前端/老记录）
    input_choice: dict = {}
    for group in details["choice"]:
        input_choice[group["key"]] = group["correct"]
        if groups_per_subject.get(group["subject"]) == 1:
            input_choice.setdefault(group["subject"], group["correct"])
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
                "choice": input_choice,
                "subjective": {str(s["qno"]): {"score": s["score"], "full": s["full"]} for s in details["subjective"]},
                "note": str(payload.get("note") or "")[:200],
            },
            # 这条记录**当时**的满分（按实际录进去的分值累加）：老记录永远按自己的满分算得分率
            "module_full": module_full_from_breakdown(details),
            # 客观题（选择）/ 主观题 / 合计 三块的分数、满分与得分率
            "sections": sections_from_breakdown(details),
            "breakdown": details,
            "schema_source": schema.get("source") or "default",
            "paper_id": schema.get("paper_id"),
        },
    }


def _section(score: float, full: float) -> dict:
    return {
        "score": round(score, 1),
        "full": round(full, 1),
        "rate": round(score / full * 100, 1) if full > 0 else 0.0,
    }


def sections_from_breakdown(details: dict) -> dict:
    """客观题（选择题）/ 主观题 / 合计 三块的小计与得分率。

    客观题和主观题是两套口径（答对个数 × 每题分值 / 逐题得分），分开算再汇总，
    这样"总分偏低"时一眼能看出是客观题还是主观题拖的。分母用**当时录进去的满分**。
    """
    choice = details.get("choice") or []
    subjectives = details.get("subjective") or []
    objective_score = sum(float(g.get("score") or 0) for g in choice)
    objective_full = sum(float(g.get("full") or 0) for g in choice)
    subjective_score = sum(float(s.get("score") or 0) for s in subjectives)
    subjective_full = sum(float(s.get("full") or 0) for s in subjectives)
    return {
        "objective": _section(objective_score, objective_full),
        "subjective": _section(subjective_score, subjective_full),
        "total": _section(objective_score + subjective_score, objective_full + subjective_full),
    }


def module_full_from_breakdown(details: dict) -> dict[str, float]:
    """从明细累加各模块满分（含 total）。"""
    fulls = {key: 0.0 for key in MODULES}
    for group in details.get("choice") or []:
        if group.get("subject") in fulls:
            fulls[group["subject"]] += float(group.get("full") or 0)
    for item in details.get("subjective") or []:
        if item.get("subject") in fulls:
            fulls[item["subject"]] += float(item.get("full") or 0)
    fulls = {key: round(value, 1) for key, value in fulls.items()}
    fulls["total"] = round(sum(fulls.values()), 1)
    return fulls


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
    # 表单结构来自这份试卷：某年 DS 只有 10 个选择题、某年 42 题是 15 分，都能正确算分
    schema = paper_structure.resolve_schema(conn, year, payload.get("paper_id"))
    computed = compute({**payload, "paper_year": year, "practice_date": practice_date}, schema)
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
    scores = {key: float(data[f"{key}_score"]) for key in MODULES}
    full = module_full_of_record(detail)
    total_full = full["total"]
    sections = (detail or {}).get("sections")
    if not sections:
        # 老记录没存 sections：按明细现算（明细一直都在）
        sections = sections_from_breakdown((detail or {}).get("breakdown") or {})
    return {
        **data,
        "total_score": float(data["total_score"]),
        **scores,
        "detail": detail,
        # 模块得分率（0~100），趋势图直接可用；分母是**这条记录自己**的满分
        "rates": {key: round(scores[key] / full[key] * 100, 1) if full.get(key) else 0.0 for key in MODULES},
        "module_full": full,
        "total_full": total_full,
        "total_rate": round(float(data["total_score"]) / total_full * 100, 1) if total_full else 0.0,
        # 客观题 / 主观题 / 合计（分数 + 满分 + 得分率），列表与录入表单都用它
        "sections": sections,
    }


def list_records(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM exam_records WHERE user_id=? ORDER BY practice_date DESC, created_at DESC, id DESC",
        (user_id,),
    ).fetchall()
    return [record_out(r) for r in rows]


def module_full_of_record(detail: dict) -> dict[str, float]:
    """算这条记录**当时**各模块的满分（含 total）。

    各年份综合题分布不同（42 题某年 13 分、某年 15 分），所以模块得分率必须按
    记录自己的满分算，不能拿固定的 45/45/35/25 去除，否则会引入偏差。
    `detail.module_full` 是算分时写进去的快照；老记录没有就按明细累加；
    再没有（最早的记录）才退回配置里的固定满分。
    """
    stored = (detail or {}).get("module_full") or {}
    fulls = {key: float(stored.get(key) or 0) for key in MODULES}
    if not any(fulls.values()):
        breakdown = (detail or {}).get("breakdown") or {}
        fulls = {key: value for key, value in module_full_from_breakdown(breakdown).items() if key in MODULES}
    if not any(fulls.values()):
        fulls = {key: float(config.MODULE_FULL_SCORE[key]) for key in MODULES}
    fulls = {key: round(value, 1) for key, value in fulls.items()}
    fulls["total"] = round(sum(fulls.values()), 1)
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
            "sections": {"objective": None, "subjective": None},
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
    section_rates = {"objective": 0.0, "subjective": 0.0}
    total = 0.0
    total_full_acc = 0.0
    samples: list[dict] = []
    for row, weight in zip(picked, weights):
        data = record_out(row)
        sample_rates = data["rates"]
        for key in MODULES:
            module_rates[key] += sample_rates[key] * weight
        section = data["sections"]
        for key in section_rates:
            section_rates[key] += float(section[key]["rate"]) * weight
        total += float(data["total_score"]) * weight
        total_full_acc += float(data["total_full"]) * weight
        samples.append(
            {
                "record_id": int(row["id"]),
                "practice_date": row["practice_date"],
                "paper_year": int(row["paper_year"]),
                "total_score": float(data["total_score"]),
                "weight": weight,
                "module_full": {key: data["module_full"][key] for key in MODULES},
                "total_full": data["total_full"],
                "rates": sample_rates,
                "sections": section,
            }
        )

    est_full = round(total_full_acc, 1) or total_full
    return {
        "count": len(picked),
        "weights": weights,
        "base_weights": base,
        "modules": {key: round(module_rates[key], 1) for key in MODULES},
        # 客观题 / 主观题 分开看（各自按记录当时的满分算得分率再加权）
        "sections": {key: round(value, 1) for key, value in section_rates.items()},
        "total": round(total, 1),
        "total_full": est_full,
        "total_rate": round(total / est_full * 100, 1) if est_full else 0.0,
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
    labels: list[str] = []
    points: list[dict] = []

    if x_axis == "paper_year":
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(int(row["paper_year"]), []).append(row)
        for year in sorted(grouped):
            bucket = grouped[year]
            outs = [record_out(r) for r in bucket]
            if aggregate == "avg":
                # 多条取平均：模块按各条自己的得分率平均，总分按原始分平均
                rates = {
                    key: round(sum(item["rates"][key] for item in outs) / len(outs), 1) for key in MODULES
                }
                values = {key: round(sum(item[key] for item in outs) / len(outs), 1) for key in MODULES}
                total_full = round(sum(item["total_full"] for item in outs) / len(outs), 1)
                sections = {
                    key: {
                        "score": round(sum(item["sections"][key]["score"] for item in outs) / len(outs), 1),
                        "full": round(sum(item["sections"][key]["full"] for item in outs) / len(outs), 1),
                        "rate": round(sum(item["sections"][key]["rate"] for item in outs) / len(outs), 1),
                    }
                    for key in ("objective", "subjective", "total")
                }
                points.append(
                    {
                        "label": str(year),
                        "year": year,
                        "count": len(bucket),
                        "practice_date": bucket[-1]["practice_date"],
                        **values,
                        "total_score": round(sum(values.values()), 1),
                        "rates": rates,
                        "sections": sections,
                        "module_full": module_full_axis(item["module_full"] for item in outs),
                        "total_full": total_full,
                    }
                )
            else:
                index = len(bucket) - 1 if aggregate == "latest" else max(
                    range(len(bucket)), key=lambda i: float(bucket[i]["total_score"])
                )
                chosen, out = bucket[index], outs[index]
                points.append(
                    {
                        "label": str(year),
                        "year": year,
                        "count": len(bucket),
                        "practice_date": chosen["practice_date"],
                        "record_id": int(chosen["id"]),
                        **{key: out[key] for key in MODULES},
                        "total_score": out["total_score"],
                        "rates": out["rates"],
                        "sections": out["sections"],
                        "module_full": {key: out["module_full"][key] for key in MODULES},
                        "total_full": out["total_full"],
                    }
                )
            labels.append(str(year))
    else:
        for row in rows:
            out = record_out(row)
            labels.append(str(row["practice_date"]))
            points.append(
                {
                    "label": str(row["practice_date"]),
                    "year": int(row["paper_year"]),
                    "count": 1,
                    "practice_date": row["practice_date"],
                    "record_id": int(row["id"]),
                    **{key: out[key] for key in MODULES},
                    "total_score": out["total_score"],
                    "rates": out["rates"],
                    "sections": out["sections"],
                    "module_full": {key: out["module_full"][key] for key in MODULES},
                    "total_full": out["total_full"],
                }
            )

    series = {key: [p["rates"][key] for p in points] for key in MODULES}
    series["total"] = [p["total_score"] for p in points]
    return {
        "x_axis": x_axis,
        "aggregate": aggregate,
        "labels": labels,
        "series": series,
        "points": points,
        # 右轴上限：取各点满分的最大值（各年满分可能不同），前端只读 total
        "module_full": module_full_axis(p["module_full"] for p in points),
        "empty": not points,
        # 曲线图上方那个小表格：始终按**做题时间**取最近 3 次，与 X 轴怎么切无关
        "estimate": estimate(conn, user_id),
    }


def module_full_axis(fulls_list) -> dict[str, float]:
    """各点的满分取最大，作为图表的“满分”参考（各年可能不一样）。"""
    out = {key: 0.0 for key in MODULES}
    for fulls in fulls_list:
        for key in MODULES:
            out[key] = max(out[key], float((fulls or {}).get(key) or 0))
    out = {key: round(value, 1) for key, value in out.items()}
    if not any(out.values()):
        return dict(config.MODULE_FULL_SCORE)
    out["total"] = round(sum(out.values()), 1)
    return out
