"""负荷快照：确认瞬间双方成员的当周权重负荷及差额。

weekly_loads / build_snapshot 是纯函数，便于单测；
save / get 负责 swap_load_snapshots 表读写。
快照一旦写入即钉死：事后改任务权重只影响后续新快照，不回写已确认单。
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS swap_load_snapshots(
  swap_id INT PRIMARY KEY,
  week_id INT,
  a_member_id INT,
  a_load INT,
  b_member_id INT,
  b_load INT,
  diff INT,
  created_at TEXT
);
"""


def init(c):
    c.executescript(SCHEMA)


def weekly_loads(slots, weights):
    """{member_id: 当周权重负荷}，负荷 = 该成员全部落位任务权重之和。"""
    loads = {}
    for s in slots:
        loads[s["member_id"]] = loads.get(s["member_id"], 0) + int(weights.get(s["task_id"], 0) or 0)
    return loads


def build_snapshot(swap, slots_after, weights):
    """以确认后落位计算双方负荷。a/b 方 = 对调申请时两格的原持有人。

    apply_swap 已交换两格成员，故确认后 A 格的现持有人是 b 方、B 格是 a 方。
    diff = a_load - b_load，三路（看板格/列表摘要/详情）同钉这一个值。
    """
    holder = {(s["day"], s["task_id"]): s["member_id"] for s in slots_after}
    a_member = holder[(swap["b_day"], swap["b_task"])]
    b_member = holder[(swap["a_day"], swap["a_task"])]
    loads = weekly_loads(slots_after, weights)
    a_load, b_load = loads.get(a_member, 0), loads.get(b_member, 0)
    return {
        "swap_id": swap["id"], "week_id": swap["week_id"],
        "a_member_id": a_member, "a_load": a_load,
        "b_member_id": b_member, "b_load": b_load,
        "diff": a_load - b_load,
    }


def save(c, snap, created_at):
    c.execute(
        "INSERT INTO swap_load_snapshots(swap_id,week_id,a_member_id,a_load,b_member_id,b_load,diff,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (snap["swap_id"], snap["week_id"], snap["a_member_id"], snap["a_load"],
         snap["b_member_id"], snap["b_load"], snap["diff"], created_at))


def get(c, swap_id):
    r = c.execute("SELECT * FROM swap_load_snapshots WHERE swap_id=?", (swap_id,)).fetchone()
    return dict(r) if r else None


def by_week(c, week_id):
    return {r["swap_id"]: dict(r) for r in c.execute(
        "SELECT * FROM swap_load_snapshots WHERE week_id=?", (week_id,))}


def all_by_swap(c):
    return {r["swap_id"]: dict(r) for r in c.execute("SELECT * FROM swap_load_snapshots")}
