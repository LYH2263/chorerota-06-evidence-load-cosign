<template>
  <div>
    <h1 class="brand">任务</h1>
    <form @submit.prevent="add">
      <input v-model="title" placeholder="任务名" />
      <button type="submit">添加</button>
    </form>
    <p class="muted">改权重只影响之后的负荷计算，不回写已确认对调上的负荷差快照</p>
    <p v-if="err" class="err">{{ err }}</p>
    <ul class="list">
      <li v-for="t in rows" :key="t.id">
        <strong>{{ t.title }}</strong>
        <span class="muted"> · {{ t.data_quality }}</span>
        权重 <input type="number" v-model.number="t.weight" style="width:80px;display:inline-block;margin:0 4px" />
        <button class="ghost" @click="save(t)">保存</button>
      </li>
    </ul>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const title = ref('')
const err = ref('')
async function load() { rows.value = await api('/tasks') }
async function add() {
  if (!title.value.trim()) return
  await api('/tasks', { method: 'POST', body: JSON.stringify({ title: title.value }) })
  title.value = ''; await load()
}
async function save(t) {
  err.value = ''
  try { await api('/tasks/' + t.id, { method: 'PUT', body: JSON.stringify({ weight: t.weight }) }); await load() }
  catch (e) { err.value = e.message }
}
onMounted(load)
</script>
