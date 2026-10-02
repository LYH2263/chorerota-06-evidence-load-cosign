<template>
  <div>
    <h1 class="brand">对调</h1>
    <p class="muted">确认时强制带证：确认成功即为两格各挂留证，并钉下双方当周负荷差</p>
    <div class="week-card" style="margin-bottom:12px">
      <label>A day <input type="number" v-model.number="form.a_day" /></label>
      <label>A task_id <input type="number" v-model.number="form.a_task" /></label>
      <label>B day <input type="number" v-model.number="form.b_day" /></label>
      <label>B task_id <input type="number" v-model.number="form.b_task" /></label>
      <button @click="request">申请对调</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <ul class="list">
      <li v-for="s in rows" :key="s.id" :class="{ voided: s.status==='void' }">
        <div>
          #{{ s.id }} D{{ s.a_day }}/T{{ s.a_task }} ↔ D{{ s.b_day }}/T{{ s.b_task }}
          <span class="chip" :class="{ coral: s.status==='pending' }">{{ statusText(s.status) }}</span>
          <span v-if="s.load_summary" class="chip">{{ s.load_summary }}</span>
          <span v-if="s.status==='confirmed'" class="chip">留证×{{ s.evidence_active }}</span>
          <button class="ghost" style="margin-left:8px" @click="toggle(s.id)">
            {{ open[s.id] ? '收起' : '详情' }}
          </button>
        </div>

        <!-- pending：确认时强制带证 -->
        <div v-if="s.status==='pending'" class="row-actions">
          <input v-model="ev[s.id].a" placeholder="A格留证（必填）" />
          <input v-model="ev[s.id].b" placeholder="B格留证（必填）" />
          <button @click="confirm(s.id)">确认改表</button>
          <button class="ghost" @click="cancel(s.id)">撤销</button>
        </div>

        <!-- confirmed：可补证、可撤销 -->
        <div v-if="s.status==='confirmed'" class="row-actions">
          <select v-model="sup[s.id].cell">
            <option value="a">补 A 格</option>
            <option value="b">补 B 格</option>
          </select>
          <input v-model="sup[s.id].note" placeholder="补证说明" />
          <button class="ghost" @click="attach(s.id)">补证</button>
          <button class="ghost" @click="cancel(s.id)">撤销对调</button>
        </div>

        <!-- 详情：负荷差快照 + 留证流水（与看板格子、列表摘要同钉） -->
        <div v-if="open[s.id] && details[s.id]" class="detail">
          <p v-if="details[s.id].load_summary" class="pin">⇄ {{ details[s.id].load_summary }}</p>
          <p v-else class="muted">无负荷差钉（{{ statusText(s.status) }}）</p>
          <p v-if="details[s.id].a_member_name" class="muted">
            {{ details[s.id].a_member_name }} 负荷 {{ details[s.id].a_load }}
            · {{ details[s.id].b_member_name }} 负荷 {{ details[s.id].b_load }}
            · 差 {{ Math.abs(details[s.id].load_diff) }}
            · 确认于 {{ details[s.id].confirmed_at }}
          </p>
          <p v-if="!details[s.id].evidences.length" class="muted">暂无留证</p>
          <ul class="list">
            <li v-for="e in details[s.id].evidences" :key="e.id">
              D{{ e.day }}/T{{ e.task_id }} · {{ e.kind }} · {{ e.note || '（无说明）' }}
              <span class="chip" :class="{ coral: e.status==='voided' }">{{ e.status }}</span>
            </li>
          </ul>
        </div>
      </li>
    </ul>
  </div>
</template>
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const err = ref('')
const form = ref({ a_day: 0, a_task: 1, b_day: 1, b_task: 1 })
const ev = reactive({})    // 每单确认用留证：ev[id] = {a, b}
const sup = reactive({})   // 每单补证：sup[id] = {cell, note}
const open = reactive({})
const details = reactive({})

function statusText(s) {
  return { pending: '待确认', confirmed: '已确认', void: '已作废' }[s] || s
}
function ensure(id) {
  if (!ev[id]) ev[id] = { a: '', b: '' }
  if (!sup[id]) sup[id] = { cell: 'a', note: '' }
}
async function load() {
  rows.value = await api('/swaps')
  rows.value.forEach(s => ensure(s.id))
}
async function refresh(id) {
  await load()
  if (open[id]) details[id] = await api('/swaps/' + id)
}
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
      body: JSON.stringify({ evidence_a: ev[id].a, evidence_b: ev[id].b }),
    })
    await refresh(id)
  } catch (e) { err.value = e.message === 'evidence_required' ? '确认时强制带证：请填写两格留证' : e.message }
}
async function cancel(id) {
  err.value = ''
  try { await api('/swaps/' + id + '/cancel', { method: 'POST', body: '{}' }); await refresh(id) }
  catch (e) { err.value = e.message }
}
async function attach(id) {
  err.value = ''
  try {
    await api('/swaps/' + id + '/evidence', { method: 'POST', body: JSON.stringify(sup[id]) })
    sup[id].note = ''
    await refresh(id)
  } catch (e) { err.value = e.message }
}
async function toggle(id) {
  open[id] = !open[id]
  if (open[id]) {
    try { details[id] = await api('/swaps/' + id) } catch (e) { err.value = e.message }
  }
}
onMounted(load)
</script>
