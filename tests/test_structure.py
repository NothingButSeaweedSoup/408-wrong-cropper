"""卷面结构（每份试卷自己的题号范围与分值）的单元测试。

为什么单独一个文件：这是「各年 408 分布不一样」的核心逻辑——
某年数据结构选择题 10 题、下一年 11 题，综合题 42 题可能是 13 分也可能是 15 分，
所以结构必须是**试卷的属性**，不能写死在 config 里。这里覆盖：
自动推导 / 校验 / 按卷面结构算分 / 老记录不被重新解释 / 管理员改结构 / OCR 读分值。

跑法：.venv\\Scripts\\python.exe tests\\test_structure.py
数据写在 data/_selftest_structure/，不污染正式库。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DATA = ROOT / "data" / "_selftest_structure"
if TEST_DATA.exists():
    shutil.rmtree(TEST_DATA, ignore_errors=True)
os.environ["ZC_DATA_DIR"] = str(TEST_DATA)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from backend import config, db  # noqa: E402
from backend.services import ocr_question, paper_structure, score_service, user_service  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and bool(condition)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------- 造数据
def make_questions(year: int, *, ds_choice: int = 10, per_score: float = 1.5,
                   subjective: list[tuple[int, str, float | None]] | None = None) -> list[dict]:
    """造一份"不标准"的卷子：DS 只有 ds_choice 个选择题、每题 per_score 分。"""
    rows: list[dict] = []
    qno = 1
    groups = [("ds", ds_choice), ("co", 11), ("os", 10), ("cn", 8)]
    for subject, count in groups:
        for _ in range(count):
            rows.append({"question_no": qno, "subject": subject, "type": "choice", "score": per_score})
            qno += 1
    if subjective is None:
        base = qno
        subjective = [(base, "ds", 12.0), (base + 1, "co", 15.0), (base + 6, "os", None)]
    for item_qno, subject, full in subjective:
        rows.append({"question_no": item_qno, "subject": subject, "type": "subjective", "score": full})
    return rows


def insert_paper(conn, year: int, questions: list[dict], title: str = "") -> int:
    cur = conn.execute(
        """INSERT INTO papers (year, title, pdf_path, status, created_at)
           VALUES (?,?,?,'split',?)""",
        (year, title or f"{year} 年真题", f"data/uploads/{year}.pdf", db.now()),
    )
    paper_id = int(cur.lastrowid)
    for row in questions:
        conn.execute(
            """INSERT INTO questions
               (paper_id, question_no, subject, type, score, order_no, bbox_json, image_paths, created_at)
               VALUES (?,?,?,?,?,?, '{}', '[]', ?)""",
            (paper_id, row["question_no"], row["subject"], row["type"], row["score"], row["question_no"], db.now()),
        )
    return paper_id


# ---------------------------------------------------------------- 测试
def test_parse_score() -> None:
    print("OCR 读题号行里的分值（各年综合题分值不同，靠这个自动填结构）")
    cases = [
        ("43. (8分) 设某计算机", 8.0),
        ("41.（10 分）", 10.0),
        ("42．(15分)某程序", 15.0),
        ("1. 设栈 S 的初始状态", None),
        ("44. (2.5分)", 2.5),
        ("45. (分)", None),
    ]
    for text, want in cases:
        got = ocr_question.parse_score(text)
        check(f"parse_score({text!r}) -> {want}", got == want, f"got={got}")


def test_from_questions() -> None:
    print("自动推导：按题目分组（各年题号范围/每题分值不同也成立）")
    rows = make_questions(2009)
    structure = paper_structure.from_questions(rows)
    check("自动推导标了 source=auto", structure["source"] == "auto", structure["source"])
    got = [(g["subject"], g["from"], g["to"], g["per_score"]) for g in structure["choice"]]
    want = [("ds", 1, 10, 1.5), ("co", 11, 21, 1.5), ("os", 22, 31, 1.5), ("cn", 32, 39, 1.5)]
    check("DS 只有 10 题也认得出来", got == want, str(got))
    check("综合题逐题一条", [s["qno"] for s in structure["subjective"]] == [40, 41, 46], str(structure["subjective"]))
    check("综合题满分取卷面读到/默认的 12", structure["subjective"][0]["full"] == 12.0,
          str(structure["subjective"][0]))
    check("没读到分值的用默认 8（46 题的默认满分）", structure["subjective"][2]["full"] == 8.0,
          str(structure["subjective"][2]))

    # 分值变了就拆组，不硬凑
    mixed = [
        {"question_no": 1, "subject": "ds", "type": "choice", "score": 2.0},
        {"question_no": 2, "subject": "ds", "type": "choice", "score": 2.0},
        {"question_no": 3, "subject": "ds", "type": "choice", "score": 1.0},
    ]
    split = paper_structure.from_questions(mixed)
    check("分值不同的题目拆成两组", [(g["from"], g["to"], g["per_score"]) for g in split["choice"]]
          == [(1, 2, 2.0), (3, 3, 1.0)], str(split["choice"]))

    fulls = paper_structure.module_full_of(structure)
    check("模块满分按结构算：DS = 10×1.5 + 12 = 27", fulls["ds"] == 27.0, str(fulls))
    check("总分 = 39×1.5 + 12 + 15 + 8 = 93.5", round(sum(fulls.values()), 1) == 93.5, str(sum(fulls.values())))


def test_normalize() -> None:
    print("结构校验：管理员改错了要能明确报错")
    bad_cases = [
        ({"choice": [{"subject": "ds", "from": 10, "to": 20, "per_score": 2},
                     {"subject": "co", "from": 15, "to": 25, "per_score": 2}]}, "同时属于"),
        ({"choice": [{"subject": "ds", "from": 0, "to": 10, "per_score": 2}]}, "范围不合法"),
        ({"choice": [{"subject": "math", "from": 1, "to": 10, "per_score": 2}]}, "科目不合法"),
        ({"choice": [{"subject": "ds", "from": 1, "to": 10, "per_score": 0}]}, "每题分值"),
        ({"subjective": [{"qno": 41, "subject": "ds", "full": 10},
                         {"qno": 41, "subject": "co", "full": 10}]}, "重复"),
        ({"choice": [{"subject": "ds", "from": 1, "to": 10, "per_score": 2}],
          "subjective": [{"qno": 5, "subject": "co", "full": 10}]}, "既是选择题又是综合题"),
    ]
    for structure, fragment in bad_cases:
        try:
            paper_structure.normalize(structure)
            check(f"{fragment} 被拦下", False, "居然通过了")
        except paper_structure.StructureError as exc:
            check(f"{fragment} 被拦下", fragment in str(exc), str(exc))

    fixed = paper_structure.normalize({
        "choice": [{"subject": "cn", "from": 32, "to": 39, "per_score": 1.5},
                   {"subject": "ds", "from": 1, "to": 10, "per_score": 1.5}],
        "subjective": [{"qno": 42, "subject": "os", "full": 12}, {"qno": 40, "subject": "ds", "full": 12}],
    })
    check("按题号排好序", [g["from"] for g in fixed["choice"]] == [1, 32] and
          [s["qno"] for s in fixed["subjective"]] == [40, 42], json.dumps(fixed, ensure_ascii=False))


def test_schema_from_paper() -> None:
    print("录入用的表单结构：来自那份试卷，没导入试卷才退回默认")
    db.init_db()
    with db.get_conn() as conn:
        paper_id = insert_paper(conn, 2011, make_questions(2011), title="2011 年真题")
        schema = paper_structure.resolve_schema(conn, 2011)
        check("拿到了试卷的结构", schema["source"] == "auto", str(schema["source"]))
        check("带上试卷 id 与标题", schema["paper_id"] == paper_id and schema["paper_title"] == "2011 年真题",
              f'{schema["paper_id"]} / {schema["paper_title"]}')
        check("题组区间来自卷面（DS 1-10）",
              (schema["choice_groups"][0]["from"], schema["choice_groups"][0]["to"],
               schema["choice_groups"][0]["per_score"]) == (1, 10, 1.5),
              str(schema["choice_groups"][0]))
        check("模块满分跟着结构", schema["module_full"]["ds"] == 27.0, str(schema["module_full"]))

        fallback = paper_structure.resolve_schema(conn, 1999)
        check("没导入过的年份退回默认结构", fallback["source"] == "default", str(fallback["source"]))
        check("默认结构还是 1-11/12-22/23-32/33-40",
              [(g["from"], g["to"]) for g in fallback["choice_groups"]] == [(1, 11), (12, 22), (23, 32), (33, 40)],
              str([(g["from"], g["to"]) for g in fallback["choice_groups"]]))

        # 同一年导了两份卷子：默认取最新那份，也可以显式指定 paper_id
        first = paper_id
        second = insert_paper(conn, 2011, make_questions(2011, ds_choice=11, per_score=2.0),
                              title="2011 年真题（另一份）")
        latest = paper_structure.resolve_schema(conn, 2011)
        check("同年多份取最新", latest["paper_id"] == second, f'latest={latest["paper_id"]} second={second}')
        picked = paper_structure.resolve_schema(conn, 2011, first)
        check("可以指定用哪一份", picked["paper_id"] == first and picked["choice_groups"][0]["to"] == 10,
              f'{picked["paper_id"]} / {picked["choice_groups"][0]}')


def test_compute_with_structure() -> None:
    print("按试卷结构算分（同一个录入值，不同结构结果不同）")
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM papers")
        conn.execute("DELETE FROM questions")
        conn.execute("DELETE FROM exam_records")
        # 2012：DS 10 题 × 1.5 分
        paper_id = insert_paper(conn, 2012, make_questions(2012), title="2012 年真题")
        user = user_service.create_user(conn, "struct_user", "secret123", "结构")
        uid = int(user["id"])

        payload = {
            "paper_year": 2012,
            "practice_date": "2026-09-19",
            "choice": {"ds": 10, "co": 11, "os": 10, "cn": 8},
            "subjective": [{"qno": 40, "score": 12, "full": 12},
                           {"qno": 41, "score": 15, "full": 15},
                           {"qno": 46, "score": 8, "full": 8}],
        }
        row = score_service.save_record(conn, uid, payload)
        out = score_service.record_out(row)
        check("DS 选择题 = 10 × 1.5 = 15，加综合题 12 = 27", out["ds"] == 27.0, str(out["ds"]))
        check("总分 = 93.5（不是默认结构的 150 满分口径）", out["total_score"] == 93.5, str(out["total_score"]))
        check("这条记录的满分是 93.5", out["total_full"] == 93.5, str(out["total_full"]))
        check("得分率按自己的满分算（满分即 100%）", out["rates"]["ds"] == 100.0, str(out["rates"]))
        check("记录里存了纸 id 与结构来源",
              out["detail"]["paper_id"] == paper_id and out["detail"]["schema_source"] == "auto",
              str(out["detail"].get("paper_id")))

        # 管理员改结构（比如 40 题其实是 10 分）：老记录不能被重新解释
        structure = paper_structure.of_paper(conn, paper_id)
        structure["subjective"] = [
            {**s, "full": 10.0} if s["qno"] == 40 else s for s in structure["subjective"]
        ]
        paper_structure.save(conn, paper_id, structure, source="manual")
        again = score_service.record_out(conn.execute("SELECT * FROM exam_records WHERE id=?", (row["id"],)).fetchone())
        check("改了结构后老记录满分不变", again["total_full"] == 93.5 and again["rates"]["ds"] == 100.0,
              f'{again["total_full"]} / {again["rates"]["ds"]}')
        check("但新结构对下一次录入生效",
              paper_structure.resolve_schema(conn, 2012)["module_full"]["ds"] == 25.0,
              str(paper_structure.resolve_schema(conn, 2012)["module_full"]))

        # 无试卷的年份：退回默认结构，照样能录
        fallback_row = score_service.save_record(conn, uid, {
            "paper_year": 1999, "practice_date": "2026-09-20",
            "choice": {"ds": 11, "co": 11, "os": 10, "cn": 8},
            "subjective": [{"qno": qno, "score": full, "full": full}
                           for qno, full in config.SUBJECTIVE_FULL_DEFAULT.items()],
        })
        fb = score_service.record_out(fallback_row)
        check("没有试卷的年份也能录（默认结构 150 分）", fb["total_score"] == 150.0 and fb["total_full"] == 150.0,
              f'{fb["total_score"]} / {fb["total_full"]}')
        check("默认结构标了 default", fb["detail"]["schema_source"] == "default",
              str(fb["detail"]["schema_source"]))

        # 趋势：两条记录满分不同，模块率各按自己的满分算
        data = score_service.trend(conn, uid)
        check("趋势里模块是百分比、总分是原始分",
              data["series"]["ds"] == [100.0, 100.0] and data["series"]["total"] == [93.5, 150.0],
              str(data["series"]))
        check("右轴上限取各点最大值", data["module_full"]["total"] == 150.0, str(data["module_full"]))


def test_multi_group_choice() -> None:
    print("同一科目多组选择题（每题分值不同）也能录")
    schema = paper_structure.to_schema(paper_structure.normalize({
        "choice": [
            {"subject": "ds", "from": 1, "to": 5, "per_score": 2},
            {"subject": "ds", "from": 6, "to": 10, "per_score": 3},
            {"subject": "co", "from": 11, "to": 20, "per_score": 2},
        ],
        "subjective": [{"qno": 41, "subject": "ds", "full": 10}],
    }))
    check("每组有自己的 key", [g["key"] for g in schema["choice_groups"]] == ["g1", "g6", "g11"],
          str([g["key"] for g in schema["choice_groups"]]))
    check("DS 满分 = 5×2 + 5×3 + 10 = 35", schema["module_full"]["ds"] == 35.0, str(schema["module_full"]))

    result = score_service.compute({
        "paper_year": 2020, "practice_date": "2026-09-19",
        "choice": {"g1": 5, "co": 10},          # 第一组按 key，计组只有一组所以按科目名也行
        "subjective": [{"qno": 41, "score": 10, "full": 10}],
    }, schema)
    check("DS = 5×2 + 0×3 + 10 = 20", result["ds_score"] == 20.0, str(result["ds_score"]))
    check("计组 = 10×2 = 20", result["co_score"] == 20.0, str(result["co_score"]))
    check("总分 = 40", result["total_score"] == 40.0, str(result["total_score"]))
    check("录入明细按分组 key 存", result["detail"]["input"]["choice"]["g1"] == 5,
          str(result["detail"]["input"]["choice"]))


def test_rebuild_and_save() -> None:
    print("管理员改题号/分值后重建结构")
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM papers")
        conn.execute("DELETE FROM questions")
        paper_id = insert_paper(conn, 2013, make_questions(2013))
        # 模拟人工校正：把 DS 的第 10 题划给计组，并改分值
        conn.execute(
            "UPDATE questions SET subject='co', score=2.0 WHERE paper_id=? AND question_no=10", (paper_id,)
        )
        rebuilt = paper_structure.rebuild(conn, paper_id)
        check("重建后 source=auto", rebuilt["source"] == "auto", rebuilt["source"])
        check("DS 变成 1-9", (rebuilt["choice"][0]["from"], rebuilt["choice"][0]["to"]) == (1, 9),
              str(rebuilt["choice"][0]))
        check("第 10 题不再算在 DS 里",
              all(not (g["subject"] == "ds" and g["from"] <= 10 <= g["to"]) for g in rebuilt["choice"]),
              str([(g["subject"], g["from"], g["to"]) for g in rebuilt["choice"]]))
        stored = conn.execute("SELECT structure_json FROM papers WHERE id=?", (paper_id,)).fetchone()[0]
        check("存进了 papers.structure_json", json.loads(stored)["choice"][0]["to"] == 9, stored[:60])

        empty = insert_paper(conn, 2014, [])
        try:
            paper_structure.rebuild(conn, empty)
            check("没有题目时给明确报错", False, "居然通过了")
        except paper_structure.StructureError as exc:
            check("没有题目时给明确报错", "还没有题目" in str(exc), str(exc))


if __name__ == "__main__":
    test_parse_score()
    test_from_questions()
    test_normalize()
    test_schema_from_paper()
    test_compute_with_structure()
    test_multi_group_choice()
    test_rebuild_and_save()
    print("\n全部通过" if ok else "\n存在失败项")
    raise SystemExit(0 if ok else 1)
