from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.rota import build_week_slots, swap_legal
from app.modules import chore_photo, swap_confirm, swap_views

app = FastAPI(title="Chorerota", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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

@app.get("/api/weeks")
def list_weeks():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM weeks")]; c.close(); return rows

@app.get("/api/weeks/{week_id}/board")
def week_board(week_id: int):
    c = connect()
    payload = swap_views.board_with_pins(c, week_id)
    c.close()
    if payload is None: raise HTTPException(404, "week not found")
    return payload

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
    c = connect(); rows = swap_views.list_swaps(c); c.close(); return rows

@app.get("/api/swaps/{swap_id}")
def swap_detail(swap_id: int):
    c = connect()
    detail = swap_views.swap_detail(c, swap_id)
    c.close()
    if detail is None: raise HTTPException(404, "swap not found")
    return detail

def _swap_http_error(code: str) -> HTTPException:
    return HTTPException(404 if code == "swap_not_found" else 400, code)

class ConfirmBody(BaseModel):
    evidence_a: str = ""
    evidence_b: str = ""

@app.post("/api/swaps/{swap_id}/confirm")
def confirm_swap(swap_id: int, body: ConfirmBody = ConfirmBody()):
    """确认时强制带证：两格留证内容缺一即 400，单保持 pending。"""
    c = connect()
    try:
        swap_confirm.confirm(c, swap_id, body.evidence_a, body.evidence_b)
        c.commit()
    except swap_confirm.SwapError as e:
        c.rollback(); c.close(); raise _swap_http_error(str(e))
    detail = swap_views.swap_detail(c, swap_id)
    c.close(); return detail

@app.post("/api/swaps/{swap_id}/cancel")
def cancel_swap(swap_id: int):
    """撤销：回滚两格成员、级联作废留证、负荷差展示随 status=void 下线。"""
    c = connect()
    try:
        swap_confirm.cancel(c, swap_id)
        c.commit()
    except swap_confirm.SwapError as e:
        c.rollback(); c.close(); raise _swap_http_error(str(e))
    detail = swap_views.swap_detail(c, swap_id)
    c.close(); return detail

class EvidenceBody(BaseModel):
    cell: str  # "a" | "b"
    note: str = ""

@app.post("/api/swaps/{swap_id}/evidence")
def attach_evidence(swap_id: int, body: EvidenceBody):
    """补证：仅 confirmed 单可挂；pending/void 强行挂证 400 且留证表不增行。"""
    c = connect()
    sw = c.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if sw is None: c.close(); raise HTTPException(404, "swap not found")
    if body.cell not in ("a", "b"): c.close(); raise HTTPException(400, "bad_cell")
    day, task = (sw["a_day"], sw["a_task"]) if body.cell == "a" else (sw["b_day"], sw["b_task"])
    member = c.execute(
        "SELECT member_id FROM assignments WHERE week_id=? AND day=? AND task_id=?",
        (sw["week_id"], day, task)).fetchone()
    try:
        eid = chore_photo.attach_for_swap(
            c, sw, day, task, member["member_id"] if member else None, body.note.strip(), kind="manual")
        c.commit()
    except chore_photo.EvidenceRejected as e:
        c.rollback(); c.close(); raise HTTPException(400, str(e))
    c.close(); return {"id": eid, "status": "active"}

@app.get("/api/settings")
def get_settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.put("/api/settings")
def put_settings(body: dict):
    c = connect()
    for k, v in body.items():
        c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    c.commit(); c.close(); return {"ok": True}
