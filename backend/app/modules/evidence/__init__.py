"""留证仓储：对调确认后两格各挂的留证记录（photo/note 等）。

只负责 evidence_records 表的读写，不做对调状态判断 ——
状态门禁（pending 拒挂、确认才允许）在 swap_confirm / API 层。
留证只可作废（status: active -> void），不物理删除，保留审计轨迹。
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_records(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  swap_id INT,
  week_id INT,
  day INT,
  task_id INT,
  member_id INT,
  kind TEXT,
  ref TEXT,
  status TEXT,
  created_at TEXT
);
"""


def init(c):
    c.executescript(SCHEMA)


def attach(c, *, swap_id, week_id, day, task_id, member_id, kind, ref, created_at):
    """挂一条留证到某格，返回新行 id。调用方须先确认对调状态允许挂证。"""
    cur = c.execute(
        "INSERT INTO evidence_records(swap_id,week_id,day,task_id,member_id,kind,ref,status,created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (swap_id, week_id, day, task_id, member_id, kind, ref, "active", created_at))
    return cur.lastrowid


def list_for_swap(c, swap_id, only_active=False):
    q = "SELECT * FROM evidence_records WHERE swap_id=?"
    if only_active:
        q += " AND status='active'"
    return [dict(r) for r in c.execute(q + " ORDER BY id", (swap_id,))]


def active_counts_by_swap(c):
    return {r["swap_id"]: r["n"] for r in c.execute(
        "SELECT swap_id, COUNT(*) n FROM evidence_records WHERE status='active' GROUP BY swap_id")}


def active_counts_by_cell(c, week_id):
    return {(r["swap_id"], r["day"], r["task_id"]): r["n"] for r in c.execute(
        "SELECT swap_id, day, task_id, COUNT(*) n FROM evidence_records"
        " WHERE week_id=? AND status='active' GROUP BY swap_id, day, task_id", (week_id,))}


def void_for_swap(c, swap_id):
    """级联作废：撤销对调时把该单全部在效留证置为 void，返回作废行数。"""
    cur = c.execute(
        "UPDATE evidence_records SET status='void' WHERE swap_id=? AND status='active'", (swap_id,))
    return cur.rowcount


def count_all(c):
    return c.execute("SELECT COUNT(*) c FROM evidence_records").fetchone()["c"]
