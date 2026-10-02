"""确认写库（swap_confirm）：对调确认/撤销的事务编排。

confirm：应用对调 → 当周负荷快照落库 → 两格各挂 confirm 留证，同一事务。
  政策拍板为「确认时强制带证」：evidence_a/evidence_b 缺一即拒，单保持 pending。
cancel：confirmed 单回滚两格成员、级联作废留证、单置 void；pending 单直接置 void。
本模块不写 commit——由调用方（API 层）统一 commit/rollback。
"""
from datetime import datetime

from app.engines import load as load_engine
from app.engines.rota import apply_swap
from app.modules import chore_photo


class SwapError(Exception):
    """对调写库被拒。message 为稳定错误码，API 层映射 HTTP 状态。"""


def _get_swap(conn, swap_id: int):
    return conn.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()


def confirm(conn, swap_id: int, evidence_a: str = "", evidence_b: str = "") -> dict:
    sw = _get_swap(conn, swap_id)
    if sw is None:
        raise SwapError("swap_not_found")
    if sw["status"] != "pending":
        raise SwapError("not_pending")
    if not (evidence_a or "").strip() or not (evidence_b or "").strip():
        raise SwapError("evidence_required")

    assigns = [
        dict(r)
        for r in conn.execute(
            "SELECT id, day, task_id, member_id FROM assignments WHERE week_id=? ORDER BY id",
            (sw["week_id"],),
        )
    ]
    slots = [{"day": a["day"], "task_id": a["task_id"], "member_id": a["member_id"]} for a in assigns]
    try:
        new_slots = apply_swap(slots, sw["a_day"], sw["a_task"], sw["b_day"], sw["b_task"])
    except ValueError as e:
        raise SwapError(str(e))

    # 对调前的占格者（即本单双方成员）
    a_member = next(s["member_id"] for s in slots if s["day"] == sw["a_day"] and s["task_id"] == sw["a_task"])
    b_member = next(s["member_id"] for s in slots if s["day"] == sw["b_day"] and s["task_id"] == sw["b_task"])

    for a, s in zip(assigns, new_slots):
        conn.execute("UPDATE assignments SET member_id=? WHERE id=?", (s["member_id"], a["id"]))

    # 快照：对调应用后的当周负荷，落库后不再随任务权重变化
    snap = load_engine.snapshot(conn, sw["week_id"], a_member, b_member)
    conn.execute(
        "UPDATE swap_requests SET status='confirmed', a_member=?, b_member=?,"
        " a_load=?, b_load=?, load_diff=?, confirmed_at=? WHERE id=?",
        (a_member, b_member, snap["a_load"], snap["b_load"], snap["load_diff"],
         datetime.now().isoformat(timespec="seconds"), swap_id),
    )

    # 两格各挂 confirm 留证；占格者已互换，A 格现属 b_member，B 格现属 a_member
    ea, eb = chore_photo.attach_confirm_pair(conn, sw, b_member, a_member, evidence_a.strip(), evidence_b.strip())
    return {
        "swap_id": swap_id, "status": "confirmed",
        "a_member": a_member, "b_member": b_member, **snap,
        "evidence_ids": [ea, eb],
    }


def cancel(conn, swap_id: int) -> dict:
    sw = _get_swap(conn, swap_id)
    if sw is None:
        raise SwapError("swap_not_found")
    if sw["status"] == "void":
        raise SwapError("not_cancellable")

    voided_evidence = 0
    if sw["status"] == "confirmed":
        # 回滚两格成员：直接互换两格当前占格者
        cells = {}
        for day, task in ((sw["a_day"], sw["a_task"]), (sw["b_day"], sw["b_task"])):
            row = conn.execute(
                "SELECT id, member_id FROM assignments WHERE week_id=? AND day=? AND task_id=?",
                (sw["week_id"], day, task),
            ).fetchone()
            if row is None:
                raise SwapError("slot_missing")
            cells[(day, task)] = dict(row)
        ra, rb = cells[(sw["a_day"], sw["a_task"])], cells[(sw["b_day"], sw["b_task"])]
        conn.execute("UPDATE assignments SET member_id=? WHERE id=?", (rb["member_id"], ra["id"]))
        conn.execute("UPDATE assignments SET member_id=? WHERE id=?", (ra["member_id"], rb["id"]))
        # 级联作废留证
        voided_evidence = chore_photo.void_for_swap(conn, swap_id)

    conn.execute(
        "UPDATE swap_requests SET status='void', voided_at=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), swap_id),
    )
    return {"swap_id": swap_id, "status": "void", "voided_evidence": voided_evidence}
