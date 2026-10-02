# Chorerota · 家庭值日轮转

底座：成员+任务 → round-robin 生成周表 → 申请对调 → 确认改表。

| 服务 | 端口 |
| --- | --- |
| 前端 | 5100 |
| API | 10100 |

```bash
docker compose up --build
pytest backend/app/tests
```

种子含 clean/dirty。0-1 空桩：`streak_badge` / `skip_week`。

## 留证连带负荷差

对调确认成功即为两格各挂留证，并把**确认瞬间**双方成员当周权重负荷与差额
快照写进对调单；看板格子、对调列表摘要、对调详情三路同钉同一文案
（`engines/load.py::load_summary` 是唯一 formatter）。事后改任务权重不回溯
已确认单的快照。撤销对调级联作废留证、回滚成员与负荷差展示。

政策二选一拍板：**确认时强制带证**（未选「确认后限时补证否则自动作废」）。
确认接口必须携带两格留证内容，缺一 `400 evidence_required`，单保持 pending；
confirmed ⟺ 两格有证，无需限时清扫器，看板与列表状态机天然一致。

模块划分：

| 模块 | 职责 |
| --- | --- |
| `app/engines/load.py` | 负荷快照：当周权重负荷、差额、三路同钉文案 |
| `app/modules/chore_photo` | 留证仓储：evidences 表、未确认拒挂、级联作废 |
| `app/modules/swap_confirm` | 确认写库：confirm/cancel 事务编排（不 commit） |
| `app/modules/swap_views` | 三路读视图：看板钉、列表摘要、详情 |

接口：

- `POST /api/swaps/{id}/confirm` `{evidence_a, evidence_b}` — 强制带证确认
- `POST /api/swaps/{id}/cancel` — 撤销，级联作废留证
- `POST /api/swaps/{id}/evidence` `{cell, note}` — 补证；pending/void 强行挂证 `400 swap_not_confirmed`，留证表不增行
- `GET /api/swaps` / `GET /api/swaps/{id}` / `GET /api/weeks/{id}/board` — 三路同钉输出
