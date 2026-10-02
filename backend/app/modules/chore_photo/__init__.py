"""留证仓储（chore_photo）：evidences 表的存储与不变量。

- 确认时强制带证：confirm 事务内为两格各挂一条 kind='confirm' 留证。
- 未确认拒挂：attach_for_swap 只认 confirmed 单，否则抛 EvidenceRejected，
  调用方（API）回滚，留证表不增行。
- 撤销级联：void_for_swap 把该单所有留证置 voided（不物理删除，留审计）。
"""
from datetime import datetime


class EvidenceRejected(Exception):
    """挂证被拒（如对调未确认）。message 为稳定错误码。"""


def init_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evidences(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            swap_id INT NOT NULL,
            week_id INT NOT NULL,
            day INT NOT NULL,
            task_id INT NOT NULL,
            member_id INT,
            kind TEXT NOT NULL DEFAULT 'manual',
            note TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT
        )
    """)


def _insert(conn, swap_id, week_id, day, task_id, member_id, kind, note) -> int:
    cur = conn.execute(
        "INSERT INTO evidences(swap_id,week_id,day,task_id,member_id,kind,note,status,created_at)"
        " VALUES (?,?,?,?,?,?,?,'active',?)",
        (swap_id, week_id, day, task_id, member_id, kind, note,
         datetime.now().isoformat(timespec="seconds")),
    )
    return cur.lastrowid


def attach_for_swap(conn, swap, day: int, task_id: int, member_id, note: str, kind: str = "manual") -> int:
    """给某格挂证。swap 为 swap_requests 行；未确认一律拒挂。"""
    if swap is None:
        raise EvidenceRejected("swap_not_found")
    if swap["status"] != "confirmed":
        raise EvidenceRejected("swap_not_confirmed")
    if (day, task_id) not in ((swap["a_day"], swap["a_task"]), (swap["b_day"], swap["b_task"])):
        raise EvidenceRejected("cell_not_in_swap")
    return _insert(conn, swap["id"], swap["week_id"], day, task_id, member_id, kind, note)


def attach_confirm_pair(conn, swap, a_member: int, b_member: int, note_a: str, note_b: str) -> tuple:
    """确认事务内调用：为两格各挂一条 confirm 留证，member 为对调后占格者。"""
    ea = _insert(conn, swap["id"], swap["week_id"], swap["a_day"], swap["a_task"], a_member, "confirm", note_a)
    eb = _insert(conn, swap["id"], swap["week_id"], swap["b_day"], swap["b_task"], b_member, "confirm", note_b)
    return ea, eb


def list_for_swap(conn, swap_id: int) -> list:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM evidences WHERE swap_id=? ORDER BY id", (swap_id,)
        )
    ]


def count_active(conn, swap_id: int, day: int = None, task_id: int = None) -> int:
    sql = "SELECT COUNT(*) c FROM evidences WHERE swap_id=? AND status='active'"
    args = [swap_id]
    if day is not None and task_id is not None:
        sql += " AND day=? AND task_id=?"
        args += [day, task_id]
    return conn.execute(sql, args).fetchone()["c"]


def void_for_swap(conn, swap_id: int) -> int:
    """级联作废：该单全部 active 留证置 voided，返回影响行数。"""
    cur = conn.execute(
        "UPDATE evidences SET status='voided' WHERE swap_id=? AND status='active'",
        (swap_id,),
    )
    return cur.rowcount
