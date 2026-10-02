import json
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.rota import build_week_slots, swap_legal, apply_swap
from app.modules import evidence, load_snapshot, swap_confirm

app = FastAPI(title="Chorerota", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "chorerota"}

@app.get("/api/members")
def list_members():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM members")]; c.close(); return rows

@app.post("/api/members")
def add_member(body: dict):
    c = connect()
    cur = c.execute("INSERT INTO members(name,active,data_quality) VALUES (?,?,?)",
                    (body.get("name","未命名"), int(body.get("active",1)), body.get("data_quality","clean")))
    c.commit(); mid = cur.lastrowid; c.close(); return {"id": mid}

@app.get("/api/tasks")
def list_tasks():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM tasks")]; c.close(); return rows

@app.post("/api/tasks")
def add_task(body: dict):
    c = connect()
    cur = c.execute("INSERT INTO tasks(title,weight,data_quality) VALUES (?,?,?)",
                    (body.get("title","任务"), int(body.get("weight",1)), body.get("data_quality","clean")))
    c.commit(); tid = cur.lastrowid; c.close(); return {"id": tid}

@app.put("/api/tasks/{task_id}")
def update_task(task_id: int, body: dict):
    c = connect()
    t = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t: c.close(); raise HTTPException(404, "task not found")
    if "title" in body:
        c.execute("UPDATE tasks SET title=? WHERE id=?", (str(body["title"]), task_id))
    if "weight" in body:
        c.execute("UPDATE tasks SET weight=? WHERE id=?", (int(body["weight"]), task_id))
    c.commit()
    row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
    c.close(); return row

@app.get("/api/weeks")
def list_weeks():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM weeks")]; c.close(); return rows

@app.get("/api/weeks/{week_id}/board")
def week_board(week_id: int):
    c = connect()
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    assigns = [dict(r) for r in c.execute("SELECT * FROM assignments WHERE week_id=?", (week_id,))]
    members = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM members")}
    tasks = {r["id"]: r["title"] for r in c.execute("SELECT id,title FROM tasks")}
    # 三路同钉·看板格：已确认对调的两格钉上同一份负荷差快照与在效留证数
    cell_swap = {}
    for sw in c.execute("SELECT * FROM swap_requests WHERE week_id=? AND status='confirmed'", (week_id,)):
        cell_swap[(sw["a_day"], sw["a_task"])] = sw["id"]
        cell_swap[(sw["b_day"], sw["b_task"])] = sw["id"]
    snaps = load_snapshot.by_week(c, week_id)
    ev_cells = evidence.active_counts_by_cell(c, week_id)
    c.close()
    for a in assigns:
        a["member_name"] = members.get(a["member_id"], "?")
        a["task_title"] = tasks.get(a["task_id"], "?")
        sid = cell_swap.get((a["day"], a["task_id"]))
        if sid and sid in snaps:
            a["swap_id"] = sid
            a["load_diff"] = snaps[sid]["diff"]
            a["evidence_count"] = ev_cells.get((sid, a["day"], a["task_id"]), 0)
    return {"week": dict(week), "assignments": assigns}

class GenBody(BaseModel):
    days: int = 7

@app.post("/api/weeks/{week_id}/generate")
def generate(week_id: int, body: GenBody = GenBody()):
    c = connect()
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    mids = [r["id"] for r in c.execute("SELECT id FROM members WHERE active=1 AND data_quality='clean' ORDER BY id")]
    tids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE data_quality='clean' AND weight>0 ORDER BY id")]
    slots = build_week_slots(mids, tids, days=body.days)
    c.execute("DELETE FROM assignments WHERE week_id=?", (week_id,))
    for s in slots:
        c.execute("INSERT INTO assignments(week_id,day,task_id,member_id) VALUES (?,?,?,?)",
                  (week_id, s["day"], s["task_id"], s["member_id"]))
    c.execute("UPDATE weeks SET status='ready' WHERE id=?", (week_id,))
    c.commit(); c.close()
    return {"count": len(slots), "slots": slots}

class SwapBody(BaseModel):
    a_day: int; a_task: int; b_day: int; b_task: int; note: str = ""

@app.post("/api/weeks/{week_id}/swaps")
def request_swap(week_id: int, body: SwapBody):
    c = connect()
    assigns = [dict(r) for r in c.execute("SELECT day,task_id,member_id FROM assignments WHERE week_id=?", (week_id,))]
    check = swap_legal(assigns, body.a_day, body.a_task, body.b_day, body.b_task)
    if not check["ok"]:
        c.close(); raise HTTPException(400, check["reason"])
    cur = c.execute(
        "INSERT INTO swap_requests(week_id,a_day,a_task,b_day,b_task,status,note) VALUES (?,?,?,?,?,?,?)",
        (week_id, body.a_day, body.a_task, body.b_day, body.b_task, "pending", body.note))
    c.commit(); sid = cur.lastrowid; c.close()
    return {"id": sid, "status": "pending", **check}

@app.get("/api/swaps")
def list_swaps():
    c = connect()
    rows = [dict(r) for r in c.execute("SELECT * FROM swap_requests ORDER BY id DESC")]
    snaps = load_snapshot.all_by_swap(c)
    ev_counts = evidence.active_counts_by_swap(c)
    c.close()
    for r in rows:
        # 三路同钉·列表摘要：仅 confirmed 单透出快照差额；撤销/未确认一律不展示
        snap = snaps.get(r["id"]) if r["status"] == "confirmed" else None
        r["load_diff"] = snap["diff"] if snap else None
        r["evidence_count"] = ev_counts.get(r["id"], 0)
    return rows

@app.get("/api/swaps/{swap_id}")
def swap_detail(swap_id: int):
    c = connect()
    sw = c.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if not sw: c.close(); raise HTTPException(404, "swap not found")
    sw = dict(sw)
    # 三路同钉·详情：快照只在 confirmed 时透出，撤销后负荷差展示随状态回滚
    sw["snapshot"] = load_snapshot.get(c, swap_id) if sw["status"] == "confirmed" else None
    sw["evidences"] = evidence.list_for_swap(c, swap_id)
    c.close()
    return sw

class ConfirmBody(BaseModel):
    evidence_a: dict | None = None
    evidence_b: dict | None = None

@app.post("/api/swaps/{swap_id}/confirm")
def confirm_swap(swap_id: int, body: ConfirmBody = ConfirmBody()):
    c = connect()
    try:
        result = swap_confirm.confirm(c, swap_id, body.evidence_a, body.evidence_b, _now())
    except swap_confirm.SwapError as e:
        c.close(); raise HTTPException(400, e.reason)
    c.commit(); c.close()
    return result

class EvidenceBody(BaseModel):
    cell: str
    kind: str = "photo"
    ref: str = ""

@app.post("/api/swaps/{swap_id}/evidence")
def attach_evidence(swap_id: int, body: EvidenceBody):
    c = connect()
    sw = c.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if not sw: c.close(); raise HTTPException(404, "swap not found")
    if sw["status"] != "confirmed":
        c.close(); raise HTTPException(400, "not_confirmed")  # pending 拒挂，留证表不增行
    if body.cell not in ("a", "b"):
        c.close(); raise HTTPException(400, "bad_cell")
    if not body.ref.strip():
        c.close(); raise HTTPException(400, "ref_required")
    day, task = (sw["a_day"], sw["a_task"]) if body.cell == "a" else (sw["b_day"], sw["b_task"])
    holder = c.execute(
        "SELECT member_id FROM assignments WHERE week_id=? AND day=? AND task_id=?",
        (sw["week_id"], day, task)).fetchone()
    if not holder: c.close(); raise HTTPException(400, "slot_missing")
    eid = evidence.attach(c, swap_id=swap_id, week_id=sw["week_id"], day=day, task_id=task,
                          member_id=holder["member_id"], kind=body.kind, ref=body.ref.strip(),
                          created_at=_now())
    c.commit(); c.close()
    return {"id": eid, "swap_id": swap_id}

@app.post("/api/swaps/{swap_id}/cancel")
def cancel_swap(swap_id: int):
    c = connect()
    try:
        result = swap_confirm.cancel(c, swap_id, _now())
    except swap_confirm.SwapError as e:
        c.close(); raise HTTPException(400, e.reason)
    c.commit(); c.close()
    return result

@app.get("/api/settings")
def get_settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.put("/api/settings")
def put_settings(body: dict):
    c = connect()
    for k, v in body.items():
        c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    c.commit(); c.close(); return {"ok": True}
