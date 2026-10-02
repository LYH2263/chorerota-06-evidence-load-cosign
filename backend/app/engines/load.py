"""负荷快照引擎：确认瞬间双方成员当周权重负荷与差额。

快照在确认时落库（swap_requests.a_load/b_load/load_diff），事后改任务权重
不回溯已确认单。load_summary() 是看板格子、对调列表、对调详情三路共用的
唯一文案来源，保证同钉。
"""


def weekly_loads(conn, week_id: int) -> dict:
    """{member_id: 当周任务权重合计}，按当前 assignments 现算。"""
    rows = conn.execute(
        "SELECT a.member_id AS mid, COALESCE(SUM(t.weight), 0) AS load "
        "FROM assignments a JOIN tasks t ON t.id = a.task_id "
        "WHERE a.week_id = ? GROUP BY a.member_id",
        (week_id,),
    )
    return {r["mid"]: r["load"] for r in rows}


def snapshot(conn, week_id: int, a_member: int, b_member: int) -> dict:
    """对调应用后的双方负荷快照；load_diff 为 a-b 有向差。"""
    loads = weekly_loads(conn, week_id)
    a_load = loads.get(a_member, 0)
    b_load = loads.get(b_member, 0)
    return {"a_load": a_load, "b_load": b_load, "load_diff": a_load - b_load}


def load_summary(name_a: str, load_a: int, name_b: str, load_b: int) -> str:
    """三路同钉文案的唯一 formatter。输入必须来自已落库的快照数字。"""
    return f"{name_a} {load_a} ↔ {name_b} {load_b} · 负荷差 {abs(load_a - load_b)}"


def summary_for_swap(conn, swap) -> str | None:
    """仅 confirmed 单出钉文案；pending/void 一律 None（撤销即回滚展示）。"""
    if swap["status"] != "confirmed":
        return None
    names = {
        r["id"]: r["name"]
        for r in conn.execute(
            "SELECT id, name FROM members WHERE id IN (?, ?)",
            (swap["a_member"], swap["b_member"]),
        )
    }
    return load_summary(
        names.get(swap["a_member"], "?"),
        swap["a_load"],
        names.get(swap["b_member"], "?"),
        swap["b_load"],
    )
