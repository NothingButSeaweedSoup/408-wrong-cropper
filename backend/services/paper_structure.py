"""每份试卷自己的卷面结构：题号范围、科目、分值。

为什么不写死在 config 里：408 各年的分布并不一致——数据结构选择题某些年 10 题、
某些年 11 题；综合题 41-47 的分值年年不同（4? 分卷面会印 "(8分)" 这种）。
所以结构是**试卷的属性**：

    {"source": "auto"|"manual",
     "choice": [{"subject": "ds", "from": 1, "to": 10, "per_score": 2.0}, ...],
     "subjective": [{"qno": 41, "subject": "ds", "full": 10.0}, ...]}

来源三条路：
1. 导入真题切完题后按 `questions` 表**自动分组**（`from_questions`）——综合题分值优先用
   OCR 从题号行里读到的 "(8分)"，没读到就用默认值；
2. 管理员在后台「试卷结构」面板里改（`normalize` 校验后存 `papers.structure_json`）；
3. 该年份还没导入试卷时，退回 `config.default_score_schema()`。

录入成绩时用这个结构生成表单、算分；已存的记录不受后续改动影响（记录自带快照）。
"""

from __future__ import annotations

import sqlite3

from .. import config, db

SUBJECT_KEYS = ("ds", "co", "os", "cn")


class StructureError(ValueError):
    """结构不合法，消息可直接给前端看。"""


# ---------------------------------------------------------------- 自动推导
def from_questions(question_rows: list[sqlite3.Row] | list[dict]) -> dict:
    """按切出来的题目自动分组。

    - 选择题：同科目、同分值、题号连续 → 合成一组（所以"某年 DS 只有 10 题"天然成立）；
      分值一旦不同就拆成两组，保留实际信息。
    - 综合题：逐题一条，满分取该题的 `score`（切题时若 OCR 读到 "(8分)" 就是这个值），
      否则用默认满分。
    """
    rows = [db.row_to_dict(r) if isinstance(r, sqlite3.Row) else dict(r) for r in question_rows]
    choices = sorted((r for r in rows if r.get("type") == "choice"), key=lambda r: int(r["question_no"]))
    subjectives = sorted((r for r in rows if r.get("type") != "choice"), key=lambda r: int(r["question_no"]))

    groups: list[dict] = []
    for row in choices:
        qno = int(row["question_no"])
        subject = row.get("subject") or "ds"
        per = float(row["score"]) if row.get("score") is not None else config.DEFAULT_CHOICE_PER_SCORE
        if groups:
            last = groups[-1]
            if last["subject"] == subject and last["per_score"] == per and last["to"] + 1 == qno:
                last["to"] = qno
                continue
        groups.append({"subject": subject, "from": qno, "to": qno, "per_score": per})

    subjective = []
    for row in subjectives:
        qno = int(row["question_no"])
        full = row.get("score")
        if full is None:
            full = config.SUBJECTIVE_FULL_DEFAULT.get(qno, 0.0)
        subjective.append({"qno": qno, "subject": row.get("subject") or "ds", "full": float(full)})

    return normalize({"source": "auto", "choice": groups, "subjective": subjective}, partial=True)


# ---------------------------------------------------------------- 校验
def normalize(structure: dict | None, *, partial: bool = False) -> dict:
    """校验并规整结构；`partial=True` 用于自动推导的结果（允许还没切到题的情况）。"""
    if not structure:
        if partial:
            return {"source": "auto", "choice": [], "subjective": []}
        raise StructureError("结构为空")

    choice_in = structure.get("choice") or []
    subjective_in = structure.get("subjective") or []
    groups: list[dict] = []
    used: dict[int, str] = {}

    for raw in choice_in:
        subject = str(raw.get("subject") or "").strip()
        if subject not in SUBJECT_KEYS:
            raise StructureError(f"选择题科目不合法：{subject!r}")
        lo = int(raw.get("from") or 0)
        hi = int(raw.get("to") or 0)
        if not 1 <= lo <= hi <= config.QNO_MAX:
            raise StructureError(f"{config.SUBJECT_NAMES[subject]} 题号范围不合法：{lo}-{hi}")
        per = float(raw.get("per_score") or 0)
        if not 0 < per <= 10:
            raise StructureError(f"{config.SUBJECT_NAMES[subject]} 每题分值应在 0~10 之间")
        for qno in range(lo, hi + 1):
            if qno in used:
                raise StructureError(f"第 {qno} 题同时属于 {config.SUBJECT_NAMES[used[qno]]} 和 {config.SUBJECT_NAMES[subject]}")
            used[qno] = subject
        groups.append({"subject": subject, "from": lo, "to": hi, "per_score": round(per, 1)})

    subjective: list[dict] = []
    seen_qno: set[int] = set()
    for raw in subjective_in:
        qno = int(raw.get("qno") or 0)
        subject = str(raw.get("subject") or "").strip()
        if not 1 <= qno <= config.QNO_MAX:
            raise StructureError(f"综合题题号不合法：{qno}")
        if qno in seen_qno:
            raise StructureError(f"第 {qno} 题重复了")
        if qno in used:
            raise StructureError(f"第 {qno} 题既是选择题又是综合题")
        if subject not in SUBJECT_KEYS:
            raise StructureError(f"第 {qno} 题科目不合法：{subject!r}")
        full = float(raw.get("full") or 0)
        if not 0 <= full <= 50:
            raise StructureError(f"第 {qno} 题满分应在 0~50 之间")
        seen_qno.add(qno)
        subjective.append({"qno": qno, "subject": subject, "full": round(full, 1)})

    if not groups and not subjective and not partial:
        raise StructureError("结构里没有任何题目")

    groups.sort(key=lambda g: g["from"])
    subjective.sort(key=lambda s: s["qno"])
    return {
        "source": str(structure.get("source") or "manual"),
        "choice": groups,
        "subjective": subjective,
    }


def module_full_of(structure: dict) -> dict[str, float]:
    """按结构算出各模块满分（各年可能不同）。不含 total，方便直接求和。"""
    fulls = {key: 0.0 for key in SUBJECT_KEYS}
    for group in structure.get("choice") or []:
        fulls[group["subject"]] += (int(group["to"]) - int(group["from"]) + 1) * float(group["per_score"])
    for item in structure.get("subjective") or []:
        fulls[item["subject"]] += float(item["full"])
    return {key: round(fulls[key], 1) for key in SUBJECT_KEYS}


def module_full_total(structure: dict) -> dict[str, float]:
    """各模块满分 + 总分（接口返回给前端用的形状）。"""
    fulls = module_full_of(structure)
    fulls["total"] = round(sum(fulls.values()), 1)
    return fulls


def choice_key(group: dict) -> str:
    """选择题分组的稳定标识（录入表单按**分组**填答对个数，不是按科目）。

    同一科目可以有多组（比如某年 DS 1-5 每题 2 分、6-10 每题 3 分），
    所以表单/接口用 `g起始题号` 作 key；只有一组时也兼容按科目名提交的旧格式。
    """
    return f"g{int(group['from'])}"


def to_schema(structure: dict, *, year: int | None = None, paper: dict | None = None) -> dict:
    """把结构转成前端用的表单结构（形状与 config.default_score_schema 一致）。"""
    choice = [
        {
            "key": choice_key(group),
            "subject": group["subject"],
            "name": config.SUBJECT_NAMES[group["subject"]],
            "from": int(group["from"]),
            "to": int(group["to"]),
            "count": int(group["to"]) - int(group["from"]) + 1,
            "per_score": float(group["per_score"]),
            "full": round((int(group["to"]) - int(group["from"]) + 1) * float(group["per_score"]), 1),
        }
        for group in structure.get("choice") or []
    ]
    subjective = [
        {
            "qno": int(item["qno"]),
            "subject": item["subject"],
            "name": config.SUBJECT_NAMES[item["subject"]],
            "full": float(item["full"]),
        }
        for item in structure.get("subjective") or []
    ]
    module_full = module_full_total(structure)
    return {
        "year": year if year is not None else (int(paper["year"]) if paper else None),
        "source": structure.get("source") or "manual",
        "paper_id": int(paper["id"]) if paper else None,
        "paper_title": (paper.get("title") or "") if paper else "",
        "choice_groups": choice,
        "subjective": subjective,
        "module_full": module_full,
        "choice_full": round(sum(g["full"] for g in choice), 1),
        "subjective_full": round(sum(s["full"] for s in subjective), 1),
    }


# ---------------------------------------------------------------- 查库
def of_paper(conn: sqlite3.Connection, paper_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    if row is None:
        return None
    stored = db.load_json(row["structure_json"], {})
    if stored:
        return {**normalize(stored, partial=True), "paper_id": int(row["id"]), "year": int(row["year"]),
                "title": row["title"] or ""}
    # 老试卷没存结构：按题目现推一份（不写库）
    questions = conn.execute("SELECT * FROM questions WHERE paper_id=? ORDER BY order_no", (paper_id,)).fetchall()
    if not questions:
        return None
    derived = from_questions(questions)
    return {**derived, "paper_id": int(row["id"]), "year": int(row["year"]), "title": row["title"] or ""}


def for_year(conn: sqlite3.Connection, year: int) -> dict | None:
    """该年份最新一份试卷的结构（同一年导了多次就取最近一次）。"""
    row = conn.execute(
        "SELECT id FROM papers WHERE year=? ORDER BY id DESC LIMIT 1", (year,)
    ).fetchone()
    if row is None:
        return None
    return of_paper(conn, int(row["id"]))


def resolve_schema(conn: sqlite3.Connection, year: int | None, paper_id: int | None = None) -> dict:
    """录入成绩时用的结构：优先试卷结构，没有就退回默认结构。"""
    found = of_paper(conn, int(paper_id)) if paper_id else (for_year(conn, int(year)) if year else None)
    if found:
        return to_schema(found, year=year or found.get("year"), paper={**found, "id": found["paper_id"]})
    fallback = config.default_score_schema(year)
    fallback["paper_id"] = None
    fallback["paper_title"] = ""
    fallback["source"] = "default"
    return fallback


def save(conn: sqlite3.Connection, paper_id: int, structure: dict, *, source: str | None = None) -> dict:
    normalized = normalize({**structure, "source": source or structure.get("source") or "manual"})
    conn.execute(
        "UPDATE papers SET structure_json=? WHERE id=?",
        (db.json.dumps(normalized, ensure_ascii=False), paper_id),
    )
    return normalized


def rebuild(conn: sqlite3.Connection, paper_id: int) -> dict:
    """按当前题目表重新推导结构（管理员改过题号/科目/分值后用）。"""
    questions = conn.execute("SELECT * FROM questions WHERE paper_id=? ORDER BY order_no", (paper_id,)).fetchall()
    if not questions:
        raise StructureError("这份试卷还没有题目，先跑一次识别/切题")
    derived = from_questions(questions)
    return save(conn, paper_id, derived, source="auto")
