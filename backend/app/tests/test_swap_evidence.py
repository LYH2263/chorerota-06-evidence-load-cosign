"""留证连带负荷差：未确认拒挂 / 确认强制带证 / 三路同钉 / 快照不溯 / 撤销级联。

纯 sqlite + 模块驱动，不依赖 fastapi；每个用例独立 DATA_DIR。
种子：成员 1阿明 2小雨 3爷爷；任务 1洗碗(w1) 2倒垃圾(w1) 3扫地(w2)。
days=1 生成 3 格：(0,1)→阿明 (0,2)→小雨 (0,3)→爷爷。
对调 (0,1)↔(0,3) 后：阿明持扫地(w2)→负荷2，爷爷持洗碗(w1)→负荷1，差1。
"""
import pytest

from app import seed
from app.db import connect
from app.engines.rota import build_week_slots
from app.modules import chore_photo, swap_confirm, swap_views

WEEK = 1


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    c = connect()
    mids = [r["id"] for r in c.execute("SELECT id FROM members WHERE active=1 AND data_quality='clean' ORDER BY id")]
    tids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE data_quality='clean' AND weight>0 ORDER BY id")]
    for s in build_week_slots(mids, tids, days=1):
        c.execute("INSERT INTO assignments(week_id,day,task_id,member_id) VALUES (?,?,?,?)",
                  (WEEK, s["day"], s["task_id"], s["member_id"]))
    c.commit()
    yield c
    c.close()


def _request_swap(c, a_day=0, a_task=1, b_day=0, b_task=3):
    cur = c.execute(
        "INSERT INTO swap_requests(week_id,a_day,a_task,b_day,b_task,status,note)"
        " VALUES (?,?,?,?,?,'pending','')",
        (WEEK, a_day, a_task, b_day, b_task))
    c.commit()
    return cur.lastrowid


def _swap(c, sid):
    return c.execute("SELECT * FROM swap_requests WHERE id=?", (sid,)).fetchone()


def _evidence_rows(c):
    return c.execute("SELECT COUNT(*) n FROM evidences").fetchone()["n"]


def _member_at(c, day, task):
    return c.execute(
        "SELECT member_id FROM assignments WHERE week_id=? AND day=? AND task_id=?",
        (WEEK, day, task)).fetchone()["member_id"]


# --- 未确认拒挂 -----------------------------------------------------------

def test_pending_swap_rejects_evidence_and_table_stays_empty(conn):
    sid = _request_swap(conn)
    with pytest.raises(chore_photo.EvidenceRejected):
        chore_photo.attach_for_swap(conn, _swap(conn, sid), 0, 1, 1, "强行挂证")
    assert _evidence_rows(conn) == 0  # 留证表不增行


def test_void_swap_also_rejects_evidence(conn):
    sid = _request_swap(conn)
    swap_confirm.confirm(conn, sid, "a证", "b证")
    swap_confirm.cancel(conn, sid)
    conn.commit()
    before = _evidence_rows(conn)
    with pytest.raises(chore_photo.EvidenceRejected):
        chore_photo.attach_for_swap(conn, _swap(conn, sid), 0, 1, 1, "强行挂证")
    assert _evidence_rows(conn) == before


# --- 确认时强制带证（政策二选一拍板项） ------------------------------------

def test_confirm_without_evidence_is_rejected(conn):
    sid = _request_swap(conn)
    with pytest.raises(swap_confirm.SwapError):
        swap_confirm.confirm(conn, sid, "", "b证")
    assert _swap(conn, sid)["status"] == "pending"
    assert _evidence_rows(conn) == 0


def test_confirm_attaches_pair_and_snapshots_load(conn):
    sid = _request_swap(conn)
    res = swap_confirm.confirm(conn, sid, "A格照片", "B格照片")
    assert res["status"] == "confirmed"

    evs = chore_photo.list_for_swap(conn, sid)
    assert len(evs) == 2  # 两格各挂一证
    by_cell = {(e["day"], e["task_id"]): e for e in evs}
    assert set(by_cell) == {(0, 1), (0, 3)}
    assert all(e["kind"] == "confirm" and e["status"] == "active" for e in evs)
    # 占格者已互换：A格现属爷爷(3)，B格现属阿明(1)
    assert by_cell[(0, 1)]["member_id"] == 3
    assert by_cell[(0, 3)]["member_id"] == 1

    # 负荷快照随确认落库：阿明2 / 爷爷1 / 差1
    d = swap_views.swap_detail(conn, sid)
    assert (d["a_member"], d["b_member"]) == (1, 3)
    assert (d["a_load"], d["b_load"], d["load_diff"]) == (2, 1, 1)


def test_confirmed_swap_accepts_supplementary_evidence(conn):
    sid = _request_swap(conn)
    swap_confirm.confirm(conn, sid, "a证", "b证")
    chore_photo.attach_for_swap(conn, _swap(conn, sid), 0, 1, 3, "补一张")
    assert chore_photo.count_active(conn, sid) == 3


# --- 三路同钉 --------------------------------------------------------------

def test_board_list_detail_pin_same_summary(conn):
    sid = _request_swap(conn)
    swap_confirm.confirm(conn, sid, "a证", "b证")
    conn.commit()
    c2 = connect()  # 换连接读，模拟三路独立请求
    board = swap_views.board_with_pins(c2, WEEK)
    pins = [a["swap_pin"]["load_summary"] for a in board["assignments"] if a["swap_pin"]]
    listed = [s["load_summary"] for s in swap_views.list_swaps(c2) if s["id"] == sid]
    detail = swap_views.swap_detail(c2, sid)["load_summary"]
    c2.close()
    assert len(pins) == 2                       # 两格都挂钉
    assert pins[0] == pins[1] == listed[0] == detail
    assert detail == "阿明 2 ↔ 爷爷 1 · 负荷差 1"


def test_weight_change_does_not_rewrite_snapshot(conn):
    sid = _request_swap(conn)
    swap_confirm.confirm(conn, sid, "a证", "b证")
    conn.commit()
    before = swap_views.swap_detail(conn, sid)["load_summary"]

    conn.execute("UPDATE tasks SET weight=9 WHERE id=3")  # 事后只改任务权重
    conn.commit()
    c2 = connect()
    board_pin = [a["swap_pin"]["load_summary"]
                 for a in swap_views.board_with_pins(c2, WEEK)["assignments"] if a["swap_pin"]][0]
    listed = [s["load_summary"] for s in swap_views.list_swaps(c2) if s["id"] == sid][0]
    detail = swap_views.swap_detail(c2, sid)["load_summary"]
    c2.close()
    assert board_pin == listed == detail == before  # 快照不溯，三路仍同钉


# --- 撤销级联 ---------------------------------------------------------------

def test_cancel_cascades_evidence_and_rolls_back_display(conn):
    sid = _request_swap(conn)
    swap_confirm.confirm(conn, sid, "a证", "b证")
    conn.commit()
    assert _member_at(conn, 0, 3) == 1  # 确认后阿明在 (0,3)

    res = swap_confirm.cancel(conn, sid)
    conn.commit()
    assert res["status"] == "void" and res["voided_evidence"] == 2

    # 成员回滚
    assert _member_at(conn, 0, 1) == 1 and _member_at(conn, 0, 3) == 3
    # 留证级联作废（不删行）
    evs = chore_photo.list_for_swap(conn, sid)
    assert len(evs) == 2 and all(e["status"] == "voided" for e in evs)
    # 负荷差展示三路回滚
    board = swap_views.board_with_pins(conn, WEEK)
    assert all(a["swap_pin"] is None for a in board["assignments"])
    row = [s for s in swap_views.list_swaps(conn) if s["id"] == sid][0]
    assert row["status"] == "void" and row["load_summary"] is None
    assert swap_views.swap_detail(conn, sid)["load_summary"] is None


def test_cancel_pending_swap_has_nothing_to_cascade(conn):
    sid = _request_swap(conn)
    res = swap_confirm.cancel(conn, sid)
    assert res["status"] == "void" and res["voided_evidence"] == 0
    assert _swap(conn, sid)["status"] == "void"
