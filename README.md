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

种子含 clean/dirty。0-1 空桩：`streak_badge` / `skip_week` / `chore_photo`。

## 留证连带负荷差

对调确认与留证、负荷快照绑定，分三个模块：

| 模块 | 职责 |
| --- | --- |
| `app/modules/evidence` | 留证仓储：`evidence_records` 表读写，只作废不删除 |
| `app/modules/load_snapshot` | 负荷快照：当周权重负荷纯函数 + `swap_load_snapshots` 钉值 |
| `app/modules/swap_confirm` | 确认写库：确认/撤销事务编排 |

**取舍拍板：「确认时强制带证」**。确认请求必须携带两格留证，同事务完成
改落位 + 负荷快照 + 两格挂证 + 状态翻转；缺证整体拒绝（`evidence_required`）。
被否决的「确认后限时补证否则自动作废」需要时钟/后台任务且引入中间态，不取。
状态机因此只有 `pending → confirmed → cancelled`，看板与列表天然一致。

- `pending` 阶段挂证一律 `400 not_confirmed`，留证表不增行；确认后可在详情补挂。
- 负荷差三路同钉：看板格子、对调列表摘要、对调详情读同一份快照；
  事后改任务权重（`PUT /api/tasks/{id}`）不回写已确认单。
- 撤销（`POST /api/swaps/{id}/cancel`）级联：回滚两格落位、作废该单全部在效留证、
  三路负荷差展示随状态回滚（快照行保留作审计）。
