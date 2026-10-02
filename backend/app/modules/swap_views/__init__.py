"""三路同钉读视图：看板格子、对调列表摘要、对调详情。

三处的负荷差文案都经 load_engine.summary_for_swap 从同一份落库快照现算，
因此文案必然一致；status 非 confirmed 时一律不出钉（撤销即回滚展示）。
"""
from app.engines import load as load_engine
from app.modules import chore_photo


def _decorate(conn, row) -> dict:
    s = dict(row)
    s["load_summary"] = load_engine.summary_for_swap(conn, row)
    s["evidence_active"] = chore_photo.count_active(conn, s["id"]) if s["status"] == "confirmed" else 0
    return s


def list_swaps(conn) -> list:
    rows = conn.execute("SELECT * FROM swap_requests ORDER BY id DESC")
    return [_decorate(conn, r) for r in rows]


def swap_detail(conn, swap_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if row is None:
        return None
    s = _decorate(conn, row)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM members")}
    s["a_member_name"] = names.get(s["a_member"], "?") if s["a_member"] is not None else None
    s["b_member_name"] = names.get(s["b_member"], "?") if s["b_member"] is not None else None
    s["evidences"] = chore_photo.list_for_swap(conn, swap_id)
    return s


def board_with_pins(conn, week_id: int) -> dict | None:
    week = conn.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if week is None:
        return None
    assigns = [dict(r) for r in conn.execute("SELECT * FROM assignments WHERE week_id=?", (week_id,))]
    members = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM members")}
    tasks = {r["id"]: r["title"] for r in conn.execute("SELECT id,title FROM tasks")}
    confirmed = [
        dict(r) for r in conn.execute(
            "SELECT * FROM swap_requests WHERE week_id=? AND status='confirmed'", (week_id,)
        )
    ]
    for a in assigns:
        a["member_name"] = members.get(a["member_id"], "?")
        a["task_title"] = tasks.get(a["task_id"], "?")
        a["swap_pin"] = None
        for sw in confirmed:
            if (a["day"], a["task_id"]) in ((sw["a_day"], sw["a_task"]), (sw["b_day"], sw["b_task"])):
                a["swap_pin"] = {
                    "swap_id": sw["id"],
                    "load_summary": load_engine.summary_for_swap(conn, sw),
                    "evidence": chore_photo.count_active(conn, sw["id"], a["day"], a["task_id"]),
                }
                break
    return {"week": dict(week), "assignments": assigns}
