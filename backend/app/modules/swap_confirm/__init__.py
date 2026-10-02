"""确认写库：对调确认 / 撤销的事务编排。

取舍拍板：采用「确认时强制带证」——确认请求必须携带两格留证，
同一事务内完成 改落位 + 负荷快照 + 两格挂证 + 状态翻转，
不存在「已确认但缺证」的中间态，看板与列表状态机因此天然一致。
（被否决的另一案「确认后限时补证否则自动作废」需要时钟/后台任务，
且引入 awaiting_evidence 中间态，0-1 底座上不取。）

状态机：pending --confirm(带证)--> confirmed --cancel--> cancelled
撤销级联：回滚两格落位、作废该单全部在效留证、负荷差展示随状态回滚
（快照行保留作审计，读口只对 confirmed 单透出）。
"""

from app.engines.rota import apply_swap
from app.modules import evidence, load_snapshot


class SwapError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _load_swap(c, swap_id):
    sw = c.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if not sw:
        raise SwapError("swap_not_found")
    return dict(sw)


def _week_assigns(c, week_id):
    return [dict(r) for r in c.execute(
        "SELECT id,day,task_id,member_id FROM assignments WHERE week_id=? ORDER BY id", (week_id,))]


def _weights(c):
    return {r["id"]: r["weight"] for r in c.execute("SELECT id,weight FROM tasks")}


def _regrid(c, swap):
    """对当周落位施加（或反向施加）同一对调，返回交换后的 slots。"""
    assigns = _week_assigns(c, swap["week_id"])
    slots = [{"day": a["day"], "task_id": a["task_id"], "member_id": a["member_id"]} for a in assigns]
    try:
        new_slots = apply_swap(slots, swap["a_day"], swap["a_task"], swap["b_day"], swap["b_task"])
    except ValueError as e:
        raise SwapError(str(e))
    for a, s in zip(assigns, new_slots):
        c.execute("UPDATE assignments SET member_id=? WHERE id=?", (s["member_id"], a["id"]))
    return new_slots


def _valid_evidence(ev):
    return bool(ev) and bool(str(ev.get("ref", "")).strip())


def confirm(c, swap_id, evidence_a, evidence_b, now):
    """确认对调：强制带证，缺证整体拒绝（状态不动、留证表不增行）。"""
    sw = _load_swap(c, swap_id)
    if sw["status"] != "pending":
        raise SwapError("not_pending")
    if not (_valid_evidence(evidence_a) and _valid_evidence(evidence_b)):
        raise SwapError("evidence_required")
    new_slots = _regrid(c, sw)
    snap = load_snapshot.build_snapshot(sw, new_slots, _weights(c))
    load_snapshot.save(c, snap, now)
    holder = {(s["day"], s["task_id"]): s["member_id"] for s in new_slots}
    for (day, task), ev in (
        ((sw["a_day"], sw["a_task"]), evidence_a),
        ((sw["b_day"], sw["b_task"]), evidence_b),
    ):
        evidence.attach(
            c, swap_id=swap_id, week_id=sw["week_id"], day=day, task_id=task,
            member_id=holder[(day, task)], kind=ev.get("kind", "photo"),
            ref=str(ev["ref"]).strip(), created_at=now)
    c.execute("UPDATE swap_requests SET status='confirmed' WHERE id=?", (swap_id,))
    return {"ok": True, "swap_id": swap_id, "snapshot": snap}


def cancel(c, swap_id, now):
    """撤销已确认对调：回滚落位，级联作废留证，负荷差展示随状态回滚。"""
    sw = _load_swap(c, swap_id)
    if sw["status"] != "confirmed":
        raise SwapError("not_confirmed")
    _regrid(c, sw)  # apply_swap 对同一对格再施加一次即回滚
    voided = evidence.void_for_swap(c, swap_id)
    c.execute("UPDATE swap_requests SET status='cancelled' WHERE id=?", (swap_id,))
    return {"ok": True, "swap_id": swap_id, "voided_evidence": voided}
