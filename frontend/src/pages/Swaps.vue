<template>
  <div>
    <h1 class="brand">对调</h1>
    <p class="muted">确认时强制带证：两格各一条留证随确认一次写入，缺证确认会被拒绝</p>
    <div class="week-card" style="margin-bottom:12px">
      <label>A day <input type="number" v-model.number="form.a_day" /></label>
      <label>A task_id <input type="number" v-model.number="form.a_task" /></label>
      <label>B day <input type="number" v-model.number="form.b_day" /></label>
      <label>B task_id <input type="number" v-model.number="form.b_task" /></label>
      <button @click="request">申请对调</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <ul class="list">
      <li v-for="s in rows" :key="s.id">
        #{{ s.id }} D{{ s.a_day }}/T{{ s.a_task }} ↔ D{{ s.b_day }}/T{{ s.b_task }}
        <span class="chip" :class="{ coral: s.status==='pending' }">{{ s.status }}</span>
        <span v-if="s.load_diff !== null && s.load_diff !== undefined" class="chip">负荷差 {{ s.load_diff }}</span>
        <span v-if="s.evidence_count" class="chip">留证×{{ s.evidence_count }}</span>
        <button v-if="s.status==='pending'" style="margin-left:8px" @click="confirmId = confirmId===s.id ? 0 : s.id">确认改表</button>
        <button v-if="s.status==='confirmed'" class="ghost" style="margin-left:8px" @click="cancel(s.id)">撤销</button>
        <button class="ghost" style="margin-left:8px" @click="toggleDetail(s.id)">详情</button>

        <div v-if="confirmId===s.id && s.status==='pending'" class="week-card" style="margin-top:8px">
          <p class="muted">确认即改表，并钉入双方当周负荷快照；两格留证随单写入。</p>
          <label>A 格留证
            <select v-model="confirmForm.a_kind"><option>photo</option><option>note</option></select>
            <input v-model="confirmForm.a_ref" placeholder="如 photo://a.jpg / 文字说明" />
          </label>
          <label>B 格留证
            <select v-model="confirmForm.b_kind"><option>photo</option><option>note</option></select>
            <input v-model="confirmForm.b_ref" placeholder="如 photo://b.jpg / 文字说明" />
          </label>
          <button @click="confirm(s.id)">带证确认</button>
        </div>

        <div v-if="detailId===s.id && detail" class="week-card" style="margin-top:8px">
          <template v-if="detail.snapshot">
            <p>
              负荷快照（确认瞬间钉死）：
              {{ detail.snapshot.a_member_id }}号成员 {{ detail.snapshot.a_load }}
              ↔ {{ detail.snapshot.b_member_id }}号成员 {{ detail.snapshot.b_load }}
              · <strong>负荷差 {{ detail.snapshot.diff }}</strong>
            </p>
          </template>
          <p v-else class="muted">无负荷快照（未确认或已撤销，展示已回滚）</p>
          <p v-if="!detail.evidences.length" class="muted">暂无留证</p>
          <div v-for="e in detail.evidences" :key="e.id" class="muted">
            留证#{{ e.id }} [{{ e.status }}] D{{ e.day }}/T{{ e.task_id }} · 成员#{{ e.member_id }} · {{ e.kind }}：{{ e.ref }}
          </div>
          <div v-if="s.status==='confirmed'" style="margin-top:8px">
            <label>补挂留证
              <select v-model="attachForm.cell"><option value="a">A 格</option><option value="b">B 格</option></select>
              <select v-model="attachForm.kind"><option>photo</option><option>note</option></select>
              <input v-model="attachForm.ref" placeholder="留证引用" />
            </label>
            <button class="ghost" @click="attach(s.id)">补挂</button>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const err = ref('')
const form = ref({ a_day: 0, a_task: 1, b_day: 1, b_task: 1 })
const confirmId = ref(0)
const confirmForm = ref({ a_kind: 'photo', a_ref: '', b_kind: 'photo', b_ref: '' })
const detailId = ref(0)
const detail = ref(null)
const attachForm = ref({ cell: 'a', kind: 'photo', ref: '' })
async function load() { rows.value = await api('/swaps') }
async function request() {
  err.value = ''
  try {
    await api('/weeks/1/swaps', { method: 'POST', body: JSON.stringify(form.value) })
    await load()
  } catch (e) { err.value = e.message }
}
async function confirm(id) {
  err.value = ''
  try {
    await api('/swaps/' + id + '/confirm', {
      method: 'POST',
      body: JSON.stringify({
        evidence_a: { kind: confirmForm.value.a_kind, ref: confirmForm.value.a_ref },
        evidence_b: { kind: confirmForm.value.b_kind, ref: confirmForm.value.b_ref },
      }),
    })
    confirmId.value = 0
    confirmForm.value = { a_kind: 'photo', a_ref: '', b_kind: 'photo', b_ref: '' }
    await load()
  } catch (e) { err.value = e.message }
}
async function cancel(id) {
  err.value = ''
  try { await api('/swaps/' + id + '/cancel', { method: 'POST', body: '{}' }); await load() }
  catch (e) { err.value = e.message }
}
async function toggleDetail(id) {
  err.value = ''
  if (detailId.value === id) { detailId.value = 0; detail.value = null; return }
  try { detail.value = await api('/swaps/' + id); detailId.value = id }
  catch (e) { err.value = e.message }
}
async function attach(id) {
  err.value = ''
  try {
    await api('/swaps/' + id + '/evidence', { method: 'POST', body: JSON.stringify(attachForm.value) })
    attachForm.value = { cell: 'a', kind: 'photo', ref: '' }
    detail.value = await api('/swaps/' + id)
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
