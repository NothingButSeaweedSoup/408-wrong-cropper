"""得分计算 / 趋势聚合 / 用户密码与登录态 的单元测试（不联网、不启服务）。

跑法：.venv\\Scripts\\python.exe tests\\test_scores.py
数据写在 data/_selftest_scores/，不污染正式库。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DATA = ROOT / "data" / "_selftest_scores"
if TEST_DATA.exists():
    shutil.rmtree(TEST_DATA, ignore_errors=True)
os.environ["ZC_DATA_DIR"] = str(TEST_DATA)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from backend import config, db  # noqa: E402
from backend.services import score_service, user_service  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and bool(condition)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


def full_marks_payload(year: int = 2009, practice_date: str = "2026-09-19", note: str = "") -> dict:
    return {
        "paper_year": year,
        "practice_date": practice_date,
        "choice": {"ds": 11, "co": 11, "os": 10, "cn": 8},
        "subjective": [{"qno": qno, "score": full, "full": full}
                       for qno, full in config.SUBJECTIVE_FULL_DEFAULT.items()],
        "note": note,
    }


def test_compute() -> None:
    print("算分：选择题按答对数 × 2 分，综合题逐题相加")
    result = score_service.compute(full_marks_payload())
    check("全对 = 150", result["total_score"] == 150, str(result["total_score"]))
    check("DS 45 / 计组 45 / OS 35 / 计网 25",
          (result["ds_score"], result["co_score"], result["os_score"], result["cn_score"]) == (45, 45, 35, 25),
          str((result["ds_score"], result["co_score"], result["os_score"], result["cn_score"])))

    partial = full_marks_payload()
    partial["choice"] = {"ds": 9, "co": 8, "os": 7, "cn": 6}     # 18 + 16 + 14 + 12 = 60
    partial["subjective"] = [
        {"qno": 41, "score": 8, "full": 10},
        {"qno": 42, "score": 10, "full": 13},
        {"qno": 43, "score": 10, "full": 13},
        {"qno": 44, "score": 6, "full": 10},
        {"qno": 45, "score": 5, "full": 7},
        {"qno": 46, "score": 6, "full": 8},
        {"qno": 47, "score": 7, "full": 9},
    ]
    r = score_service.compute(partial)
    check("DS = 18 + 8 + 10 = 36", r["ds_score"] == 36, str(r["ds_score"]))
    check("计组 = 16 + 10 + 6 = 32", r["co_score"] == 32, str(r["co_score"]))
    check("OS = 14 + 5 + 6 = 25", r["os_score"] == 25, str(r["os_score"]))
    check("计网 = 12 + 7 = 19", r["cn_score"] == 19, str(r["cn_score"]))
    check("总分 = 112", r["total_score"] == 112, str(r["total_score"]))
    check("明细里保留了录入口径", r["detail"]["input"]["choice"]["ds"] == 9
          and r["detail"]["input"]["subjective"]["41"]["score"] == 8)

    print("算分：非法输入要报错")
    for label, payload in [
        ("答对数超范围", {**full_marks_payload(), "choice": {"ds": 12, "co": 11, "os": 10, "cn": 8}}),
        ("答对数为负", {**full_marks_payload(), "choice": {"ds": -1, "co": 11, "os": 10, "cn": 8}}),
        ("得分超过满分", {**full_marks_payload(),
                          "subjective": [{"qno": 41, "score": 12, "full": 10}]}),
        ("年份离谱", {**full_marks_payload(), "paper_year": 1800}),
        ("日期格式错", {**full_marks_payload(), "practice_date": "2026/09/19"}),
    ]:
        try:
            score_service.validate_meta(payload)
            score_service.compute(payload)
            check(label + " 被拦下", False, "竟然通过了")
        except score_service.ScoreError as exc:
            check(label + " 被拦下", True, str(exc))

    print("表单结构：选择题 40 题 80 分 + 综合题 70 分 = 150")
    schema = config.score_form_schema()
    check("选择题合计 80", schema["choice_full"] == 80, str(schema["choice_full"]))
    check("综合题合计 70", schema["subjective_full"] == 70, str(schema["subjective_full"]))
    check("四组选择题区间", [(g["from"], g["to"]) for g in schema["choice_groups"]]
          == [(1, 11), (12, 22), (23, 32), (33, 40)],
          str([(g["from"], g["to"]) for g in schema["choice_groups"]]))


def test_sections() -> None:
    """客观题 / 主观题 分开算，再汇总（含比例）。

    以前前端表单只把选择题加起来当总分，主观题白填了；这里把口径钉在服务端，
    顺便保证老记录（detail_json 里没有 sections 字段）也能现算出来。
    """
    print("客观题 / 主观题 / 汇总（含比例）")
    payload = {
        **full_marks_payload(2009, "2026-09-19"),
        "choice": {"ds": 9, "co": 8, "os": 7, "cn": 6},          # 60 / 80
        "subjective": [{"qno": 41, "score": 8, "full": 10}, {"qno": 42, "score": 10, "full": 13},
                       {"qno": 43, "score": 10, "full": 13}, {"qno": 44, "score": 6, "full": 10},
                       {"qno": 45, "score": 5, "full": 7}, {"qno": 46, "score": 6, "full": 8},
                       {"qno": 47, "score": 7, "full": 9}],       # 52 / 70
    }
    result = score_service.compute(payload)
    sections = result["detail"]["sections"]
    check("客观题 60/80 = 75%", sections["objective"] == {"score": 60.0, "full": 80.0, "rate": 75.0},
          str(sections["objective"]))
    check("主观题 52/70 = 74.3%", sections["subjective"] == {"score": 52.0, "full": 70.0, "rate": 74.3},
          str(sections["subjective"]))
    check("合计 112/150 = 74.7%", sections["total"] == {"score": 112.0, "full": 150.0, "rate": 74.7},
          str(sections["total"]))
    check("总分 = 客观 + 主观",
          sections["total"]["score"] == sections["objective"]["score"] + sections["subjective"]["score"]
          and sections["total"]["full"] == sections["objective"]["full"] + sections["subjective"]["full"])
    check("总分与入库口径一致", sections["total"]["score"] == result["total_score"], str(result["total_score"]))

    db.init_db()
    with db.get_conn() as conn:
        user = user_service.create_user(conn, "sec_user", "secret123", "分块")
        uid = int(user["id"])
        row = score_service.save_record(conn, uid, payload)
        out = score_service.record_out(row)
        check("记录里带 sections", out["sections"]["subjective"]["score"] == 52.0, str(out["sections"]))

        # 老记录：detail_json 里没有 sections（这次改动之前存的），要按 breakdown 现算
        detail = dict(out["detail"])
        detail.pop("sections", None)
        conn.execute("UPDATE exam_records SET detail_json=? WHERE id=?",
                     (json.dumps(detail, ensure_ascii=False), int(row["id"])))
        old = score_service.record_out(
            conn.execute("SELECT * FROM exam_records WHERE id=?", (int(row["id"]),)).fetchone()
        )
        check("老记录也能算出 sections", old["sections"]["total"] == sections["total"], str(old["sections"]))

        est = score_service.estimate(conn, uid)
        check("水平估计里客观/主观分开", est["sections"]["objective"] == 75.0 and est["sections"]["subjective"] == 74.3,
              str(est["sections"]))
        data = score_service.trend(conn, uid)
        check("趋势点里也带 sections",
              data["points"][0]["sections"]["objective"]["rate"] == 75.0,
              str(data["points"][0]["sections"]))


def test_records_and_trend() -> None:
    print("入库 + 列表 + 趋势")
    db.init_db()
    with db.get_conn() as conn:
        user = user_service.create_user(conn, "tester", "secret123", "小测")
        uid = int(user["id"])

        score_service.save_record(conn, uid, full_marks_payload(2009, "2026-09-17", "第一次"))
        score_service.save_record(conn, uid, {
            **full_marks_payload(2011, "2026-09-21"),
            "choice": {"ds": 8, "co": 9, "os": 8, "cn": 6},
        })
        score_service.save_record(conn, uid, {
            **full_marks_payload(2010, "2026-09-25"),
            "choice": {"ds": 10, "co": 10, "os": 9, "cn": 7},
        })
        # 同一年份再来一次，用来验证 paper_year 的聚合
        score_service.save_record(conn, uid, full_marks_payload(2009, "2026-10-01", "二刷"))

        records = score_service.list_records(conn, uid)
        check("列表 4 条", len(records) == 4, str(len(records)))
        check("按做题时间倒序", records[0]["practice_date"] == "2026-10-01", records[0]["practice_date"])
        check("带得分率", records[0]["rates"]["ds"] == 100.0 and records[0]["total_rate"] == 100.0,
              str(records[0]["rates"]))

        t = score_service.trend(conn, uid, "practice_date")
        check("做题时间轴 4 个点", len(t["labels"]) == 4, str(t["labels"]))
        check("按时间升序", t["labels"] == ["2026-09-17", "2026-09-21", "2026-09-25", "2026-10-01"], str(t["labels"]))
        check("模块是百分比、总分是绝对分",
              t["series"]["ds"][0] == 100.0 and t["series"]["total"][1] < 150,
              f'{t["series"]["ds"][0]} / {t["series"]["total"][1]}')

        latest = score_service.trend(conn, uid, "paper_year", "latest")
        check("年份轴 3 个点（2009 同年两条取最近）", latest["labels"] == ["2009", "2010", "2011"], str(latest["labels"]))
        check("2009 取到二刷的满分", latest["series"]["total"][0] == 150, str(latest["series"]["total"][0]))
        check("同年记录数标出来", latest["points"][0]["count"] == 2, str(latest["points"][0]["count"]))

        avg = score_service.trend(conn, uid, "paper_year", "avg")
        check("平均：2009 两次都是满分 -> 150", avg["series"]["total"][0] == 150, str(avg["series"]["total"][0]))

        best = score_service.trend(conn, uid, "paper_year", "max")
        check("最高：2011 是 4 条里最低的那次", best["series"]["total"][2] < 150, str(best["series"]["total"][2]))

        other = user_service.create_user(conn, "other", "secret123", "另一个")
        check("别人的记录看不到", score_service.list_records(conn, int(other["id"])) == [])

        created = score_service.save_record(conn, uid, full_marks_payload(2012, "2026-11-01"))
        updated = score_service.save_record(conn, uid, {
            **full_marks_payload(2012, "2026-11-02"),
            "choice": {"ds": 0, "co": 0, "os": 0, "cn": 0},
            "subjective": [],
        }, record_id=int(created["id"]))
        check("改记录会重算", updated["total_score"] == 0 and updated["practice_date"] == "2026-11-02",
              f'{updated["total_score"]} / {updated["practice_date"]}')

        try:
            score_service.save_record(conn, int(other["id"]), full_marks_payload(), record_id=int(created["id"]))
            check("不能改别人的记录", False, "竟然改成功了")
        except score_service.ScoreError as exc:
            check("不能改别人的记录", True, str(exc))


def test_users() -> None:
    print("用户：密码哈希 + 登录态")
    hashed = user_service.hash_password("secret123")
    check("哈希格式", hashed.startswith("pbkdf2_sha256$200000$"), hashed[:28])
    check("密码校验通过", user_service.verify_password("secret123", hashed))
    check("错误密码不通过", not user_service.verify_password("secret124", hashed))
    check("同一密码两次哈希不同（有盐）", user_service.hash_password("secret123") != hashed)

    db.init_db()
    with db.get_conn() as conn:
        try:
            user_service.create_user(conn, "tester", "secret123")
            check("重名被拒", False, "竟然建成功了")
        except user_service.AuthError as exc:
            check("重名被拒", True, str(exc))
        for label, name, pwd in [("用户名太短", "a", "secret123"), ("密码太短", "abcd", "123")]:
            try:
                user_service.create_user(conn, name, pwd)
                check(label + " 被拦下", False, "竟然通过了")
            except user_service.AuthError as exc:
                check(label + " 被拦下", True, str(exc))

        token_info = user_service.issue_token(conn, int(conn.execute(
            "SELECT id FROM users WHERE username='tester'").fetchone()["id"]))
        check("token 能换回用户", user_service.user_by_token(conn, token_info["token"])["username"] == "tester")
        user_service.revoke(conn, token_info["token"])
        check("登出后 token 失效", user_service.user_by_token(conn, token_info["token"]) is None)

        tester_id = int(conn.execute("SELECT id FROM users WHERE username='tester'").fetchone()["id"])
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            ("expired-token", tester_id, "2020-01-01 00:00:00", "2020-01-02 00:00:00"),
        )
        check("过期 token 失效", user_service.user_by_token(conn, "expired-token") is None)
        check("bearer 解析", user_service.bearer_token("Bearer abc") == "abc"
              and user_service.bearer_token("abc") == "abc" and user_service.bearer_token(None) == "")


def test_level_estimate() -> None:
    """当前水平估计：最近三次 50/35/15，不足三次归一化；总分用原始分，模块先算得分率。"""
    print("当前水平估计")
    db.init_db()
    with db.get_conn() as conn:
        user = user_service.create_user(conn, "est_user", "secret123", "估计")
        uid = int(user["id"])

        empty = score_service.estimate(conn, uid)
        check("没有记录时为空", empty["count"] == 0 and empty["total"] is None, str(empty["message"]))

        # 第 1 次：满分 150
        first = score_service.save_record(conn, uid, full_marks_payload(2009, "2026-09-17"))
        est = score_service.estimate(conn, uid)
        check("1 次：权重 100%", est["weights"] == [1.0], str(est["weights"]))
        check("1 次：总分就是那次的分", est["total"] == 150.0 and est["total_rate"] == 100.0,
              f'{est["total"]} / {est["total_rate"]}%')
        check("1 次：模块都是 100%", set(est["modules"].values()) == {100.0}, str(est["modules"]))
        check("权重归一化提示", "归一化" in est["message"], est["message"])

        # 第 2 次（更新）：选择题全对 80 + 综合题 0 = 80 分
        second = score_service.save_record(conn, uid, {
            **full_marks_payload(2010, "2026-09-20"),
            "subjective": [{"qno": s["qno"], "score": 0, "full": s["full"]}
                           for s in config.score_form_schema()["subjective"]],
        })
        est = score_service.estimate(conn, uid)
        check("2 次：最近 50/85、次近 35/85",
              abs(est["weights"][0] - 50 / 85) < 1e-4 and abs(est["weights"][1] - 35 / 85) < 1e-4,
              str(est["weights"]))
        expected_total = round(80 * 50 / 85 + 150 * 35 / 85, 1)
        check(f"2 次：总分 = 80×50/85 + 150×35/85 = {expected_total}", est["total"] == expected_total,
              str(est["total"]))
        # 数据结构：最近一次 22/45、次近 45/45
        expected_ds = round(round(22 / 45 * 100, 1) * 50 / 85 + 100 * 35 / 85, 1)
        check(f"2 次：数据结构得分率加权 = {expected_ds}%", est["modules"]["ds"] == expected_ds,
              str(est["modules"]["ds"]))
        check("样本带日期与权重", [s["practice_date"] for s in est["samples"]] == ["2026-09-20", "2026-09-17"],
              str([s["practice_date"] for s in est["samples"]]))

        # 第 3、4 次：验证用满三次、且只取最近三次
        score_service.save_record(conn, uid, {**full_marks_payload(2011, "2026-09-23"),
                                              "choice": {"ds": 5, "co": 5, "os": 5, "cn": 4}})
        score_service.save_record(conn, uid, {**full_marks_payload(2012, "2026-09-25"),
                                              "choice": {"ds": 0, "co": 0, "os": 0, "cn": 0},
                                              "subjective": []})
        est = score_service.estimate(conn, uid)
        check("满 3 次后权重是 50/35/15", est["weights"] == [0.5, 0.35, 0.15], str(est["weights"]))
        check("只取最近三次（最老的 09-17 被挤掉）",
              [s["practice_date"] for s in est["samples"]] == ["2026-09-25", "2026-09-23", "2026-09-20"],
              str([s["practice_date"] for s in est["samples"]]))
        check("三次齐全时不再提示归一化", est["message"] == "", est["message"])

        # 模块得分率要用"那条记录当时的满分"，不是固定的 45
        odd = user_service.create_user(conn, "est_odd", "secret123", "满分不同")
        score_service.save_record(conn, int(odd["id"]), {
            **full_marks_payload(2013, "2026-10-01"),
            "subjective": [{"qno": 41, "score": 15, "full": 15}] + [
                {"qno": s["qno"], "score": s["full"], "full": s["full"]}
                for s in config.score_form_schema()["subjective"] if s["qno"] != 41
            ],
        })
        est_odd = score_service.estimate(conn, int(odd["id"]))
        # 41 题满分改成 15 后，数据结构满分 = 22 + 15 + 13 = 50，得分 22+15+13 = 50 -> 100%
        check("按记录当时的满分算得分率（DS 100%，而不是 111%）", est_odd["modules"]["ds"] == 100.0,
              str(est_odd["modules"]["ds"]))
        check("样本里带出当时的模块满分", est_odd["samples"][0]["module_full"]["ds"] == 50.0,
              str(est_odd["samples"][0]["module_full"]))


def test_admin_flags() -> None:
    """管理员标记：.env 名单在启动时同步到库里，名单外的账号保持普通用户。"""
    print("管理员标记（.env 名单 -> users.is_admin）")
    db.init_db()
    original = config.ADMIN_USERS
    try:
        config.ADMIN_USERS = frozenset({"tester"})
        with db.get_conn() as conn:
            result = user_service.sync_admin_users(conn)
            check("sync 提升名单里的账号", result["promoted"] == ["tester"], str(result))
            check("没注册的名字会被报出来", "nobody" not in result["missing"])
            tester = conn.execute("SELECT * FROM users WHERE username='tester'").fetchone()
            other = conn.execute("SELECT * FROM users WHERE username='other'").fetchone()
            check("tester 是管理员", user_service.is_admin(tester))
            check("other 仍是普通用户", not user_service.is_admin(other))
            check("public_user 带出 is_admin", user_service.public_user(tester)["is_admin"] is True)

            again = user_service.sync_admin_users(conn)
            check("已提升的不重复提升", again["promoted"] == [], str(again))

            config.ADMIN_USERS = frozenset({"ghost"})
            ghost = user_service.sync_admin_users(conn)
            check("名单里没注册的名字进 missing", ghost["missing"] == ["ghost"], str(ghost))

            user_service.set_admin(conn, int(other["id"]), True)
            other2 = conn.execute("SELECT * FROM users WHERE id=?", (int(other["id"]),)).fetchone()
            check("手动设管理员", user_service.is_admin(other2))
            user_service.set_admin(conn, int(other["id"]), False)
    finally:
        config.ADMIN_USERS = original


if __name__ == "__main__":
    test_compute()
    test_records_and_trend()
    test_sections()
    test_level_estimate()
    test_users()
    test_admin_flags()
    print("\n全部通过" if ok else "\n存在失败项")
    raise SystemExit(0 if ok else 1)
