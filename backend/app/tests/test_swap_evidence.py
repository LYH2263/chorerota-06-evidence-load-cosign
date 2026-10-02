"""留证连带负荷差：未确认拒挂 / 确认强制带证 / 三路同钉 / 撤销级联。

种子数据下生成周表后：成员1 每天洗碗(w1)、成员2 每天倒垃圾(w1)、成员3 每天扫地(w2)。
对调 (day0,task1) ↔ (day0,task3) 确认后：
  成员1 负荷 = 6×1 + 2 = 8，成员3 负荷 = 6×2 + 1 = 13，diff = 8 - 13 = -5。
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import seed
    seed.init_db()
    from app.main import app
    with TestClient(app) as tc:
        yield tc


def _generate(client):
    r = client.post("/api/weeks/1/generate", json={"days": 7})
    assert r.status_code == 200


def _make_swap(client):
    r = client.post("/api/weeks/1/swaps", json={"a_day": 0, "a_task": 1, "b_day": 0, "b_task": 3})
    assert r.status_code == 200
    return r.json()["id"]


def _confirm(client, sid):
    return client.post(f"/api/swaps/{sid}/confirm", json={
        "evidence_a": {"kind": "photo", "ref": "a.jpg"},
        "evidence_b": {"kind": "photo", "ref": "b.jpg"},
    })


def _evidence_rows():
    from app.db import connect
    c = connect()
    n = c.execute("SELECT COUNT(*) c FROM evidence_records").fetchone()["c"]
    c.close()
    return n


def _board_cells(client):
    board = client.get("/api/weeks/1/board").json()["assignments"]
    return {(a["day"], a["task_id"]): a for a in board}


def _swap_row(client, sid):
    return next(s for s in client.get("/api/swaps").json() if s["id"] == sid)


def test_weekly_loads_pure():
    from app.modules import load_snapshot
    loads = load_snapshot.weekly_loads(
        [{"member_id": 1, "task_id": 10}, {"member_id": 1, "task_id": 20}, {"member_id": 2, "task_id": 10}],
        {10: 2, 20: 3})
    assert loads == {1: 5, 2: 2}


def test_pending_rejects_evidence_attach(client):
    """pending 阶段强行挂证：失败且留证表不增行。"""
    _generate(client)
    sid = _make_swap(client)
    before = _evidence_rows()
    r = client.post(f"/api/swaps/{sid}/evidence", json={"cell": "a", "kind": "photo", "ref": "x.jpg"})
    assert r.status_code == 400
    assert r.json()["detail"] == "not_confirmed"
    assert _evidence_rows() == before


def test_confirm_requires_evidence(client):
    """确认时强制带证：缺证确认被拒，状态不动、留证表不增行。"""
    _generate(client)
    sid = _make_swap(client)
    r = client.post(f"/api/swaps/{sid}/confirm", json={})
    assert r.status_code == 400
    assert r.json()["detail"] == "evidence_required"
    assert _swap_row(client, sid)["status"] == "pending"
    assert _evidence_rows() == 0


def test_three_way_pinned_and_snapshot_immune_to_weight_edit(client):
    """看板格 / 列表摘要 / 详情三路同钉同一负荷差；事后改权重不改快照。"""
    _generate(client)
    sid = _make_swap(client)
    r = _confirm(client, sid)
    assert r.status_code == 200

    detail = client.get(f"/api/swaps/{sid}").json()
    snap = detail["snapshot"]
    assert (snap["a_member_id"], snap["a_load"]) == (1, 8)
    assert (snap["b_member_id"], snap["b_load"]) == (3, 13)
    assert snap["diff"] == -5
    # 两格各挂一条留证：A 格现持有人为成员3，B 格为成员1
    evs = {(e["day"], e["task_id"]): e for e in detail["evidences"]}
    assert len(evs) == 2
    assert evs[(0, 1)]["member_id"] == 3 and evs[(0, 3)]["member_id"] == 1
    assert all(e["status"] == "active" for e in evs.values())

    cells = _board_cells(client)
    assert cells[(0, 1)]["load_diff"] == -5 and cells[(0, 3)]["load_diff"] == -5
    assert cells[(0, 1)]["evidence_count"] == 1 and cells[(0, 3)]["evidence_count"] == 1
    row = _swap_row(client, sid)
    assert row["load_diff"] == row["load_diff"] == snap["diff"] == -5
    assert row["evidence_count"] == 2

    # 事后只改任务权重：三路快照值纹丝不动
    r = client.put("/api/tasks/3", json={"weight": 5})
    assert r.status_code == 200 and r.json()["weight"] == 5
    assert client.get(f"/api/swaps/{sid}").json()["snapshot"]["diff"] == -5
    cells = _board_cells(client)
    assert cells[(0, 1)]["load_diff"] == -5 and cells[(0, 3)]["load_diff"] == -5
    assert _swap_row(client, sid)["load_diff"] == -5


def test_attach_after_confirm_allowed(client):
    """确认后允许补挂留证（同一格可多条）。"""
    _generate(client)
    sid = _make_swap(client)
    _confirm(client, sid)
    r = client.post(f"/api/swaps/{sid}/evidence", json={"cell": "b", "kind": "note", "ref": "已交接"})
    assert r.status_code == 200
    detail = client.get(f"/api/swaps/{sid}").json()
    assert len(detail["evidences"]) == 3
    assert _swap_row(client, sid)["evidence_count"] == 3


def test_cancel_cascades_evidence_and_rolls_back(client):
    """撤销：级联作废留证、三路负荷差展示回滚、落位回滚；留证行保留审计。"""
    _generate(client)
    sid = _make_swap(client)
    _confirm(client, sid)
    r = client.post(f"/api/swaps/{sid}/cancel")
    assert r.status_code == 200
    assert r.json()["voided_evidence"] == 2

    detail = client.get(f"/api/swaps/{sid}").json()
    assert detail["status"] == "cancelled"
    assert detail["snapshot"] is None  # 详情负荷差展示回滚
    assert len(detail["evidences"]) == 2  # 作废不删除
    assert all(e["status"] == "void" for e in detail["evidences"])

    row = _swap_row(client, sid)
    assert row["load_diff"] is None and row["evidence_count"] == 0  # 列表摘要回滚

    cells = _board_cells(client)
    assert "load_diff" not in cells[(0, 1)] and "load_diff" not in cells[(0, 3)]  # 看板回滚
    assert cells[(0, 1)]["member_id"] == 1 and cells[(0, 3)]["member_id"] == 3  # 落位回滚


def test_cancel_only_from_confirmed(client):
    """状态机：pending 不可撤销，cancelled 不可再确认。"""
    _generate(client)
    sid = _make_swap(client)
    assert client.post(f"/api/swaps/{sid}/cancel").status_code == 400
    _confirm(client, sid)
    client.post(f"/api/swaps/{sid}/cancel")
    r = _confirm(client, sid)
    assert r.status_code == 400 and r.json()["detail"] == "not_pending"
