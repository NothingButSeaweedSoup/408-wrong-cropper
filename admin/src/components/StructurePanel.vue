<script setup>
/**
 * 试卷结构：这份卷子的题号范围与分值。
 *
 * 为什么要有它：408 各年分布不一样——数据结构选择题某些年 10 题、某些年 11 题，
 * 综合题 41-47 的分值年年不同。切题时已经自动按题目分好组（主观题分值还会尝试从
 * 卷面 "(8分)" 里读），这里只是给管理员一个改错的地方：改完保存，用户端录成绩的表单
 * 和算分就按这份结构走；老记录不受影响（每条记录自带当时的满分快照）。
 */
import { computed, ref, watch } from "vue";
import { api } from "../api.js";

const props = defineProps({
  paper: { type: Object, required: true },
});
const emit = defineEmits(["refresh", "notify"]);

const SUBJECTS = [
  ["ds", "数据结构"],
  ["co", "计算机组成原理"],
  ["os", "操作系统"],
  ["cn", "计算机网络"],
];
const SUBJECT_FULL = { ds: 45, co: 45, os: 35, cn: 25 };

const structure = ref(null);
const loading = ref(false);
const saving = ref(false);

async function load() {
  loading.value = true;
  try {
    structure.value = await api.getStructure(props.paper.id);
  } catch (err) {
    structure.value = null;
    emit("notify", `结构加载失败：${err.message}`, true);
  } finally {
    loading.value = false;
  }
}

watch(() => props.paper.id, load, { immediate: true });
watch(() => props.paper.status, (now, before) => {
  if (now !== before && (now === "split" || now === "corrected")) load();
});

const choice = computed(() => structure.value?.choice || []);
const subjective = computed(() => structure.value?.subjective || []);

const totals = computed(() => {
  const module = { ds: 0, co: 0, os: 0, cn: 0 };
  for (const g of choice.value) {
    module[g.subject] += (Number(g.to) - Number(g.from) + 1) * Number(g.per_score || 0);
  }
  for (const s of subjective.value) module[s.subject] += Number(s.full || 0);
  const total = Object.values(module).reduce((a, b) => a + b, 0);
  return { module, total: Math.round(total * 10) / 10 };
});

const choiceFull = computed(() =>
  Math.round(
    choice.value.reduce((sum, g) => sum + (Number(g.to) - Number(g.from) + 1) * Number(g.per_score || 0), 0) * 10
  ) / 10
);
const subjectiveFull = computed(() =>
  Math.round(subjective.value.reduce((sum, s) => sum + Number(s.full || 0), 0) * 10) / 10
);

function addChoice() {
  const last = choice.value[choice.value.length - 1];
  const from = last ? Number(last.to) + 1 : 1;
  choice.value.push({ subject: last?.subject || "ds", from, to: from, per_score: 2 });
}

function addSubjective() {
  const last = subjective.value[subjective.value.length - 1];
  const qno = last ? Number(last.qno) + 1 : 41;
  subjective.value.push({ qno, subject: "ds", full: 10 });
}

async function save() {
  if (!structure.value) return;
  saving.value = true;
  try {
    const payload = {
      source: "manual",
      choice: choice.value.map((g) => ({
        subject: g.subject,
        from: Number(g.from),
        to: Number(g.to),
        per_score: Number(g.per_score),
      })),
      subjective: subjective.value.map((s) => ({
        qno: Number(s.qno),
        subject: s.subject,
        full: Number(s.full),
      })),
    };
    structure.value = await api.saveStructure(props.paper.id, payload);
    emit("refresh", "试卷结构已保存（用户端录成绩会按新结构算分）");
  } catch (err) {
    emit("notify", `保存失败：${err.message}`, true);
  } finally {
    saving.value = false;
  }
}

async function rebuild() {
  if (!confirm("按当前题目表重新推导结构？管理员手改的范围/分值会被覆盖。")) return;
  saving.value = true;
  try {
    structure.value = await api.rebuildStructure(props.paper.id);
    emit("refresh", "已按题目重建结构");
  } catch (err) {
    emit("notify", `重建失败：${err.message}`, true);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="structure">
    <div v-if="loading" class="placeholder">加载中…</div>
    <div v-else-if="!structure" class="placeholder">
      这份真题还没有题目（或还没识别完），先跑一次「重新识别」。
    </div>
    <template v-else>
      <div class="qtable-head">
        <span>
          卷面结构
          <span class="tag" :class="{ manual: structure.source === 'manual' }">
            {{ structure.source === "manual" ? "管理员改过" : "自动推导" }}
          </span>
        </span>
        <span class="hint">
          选择题按题组填答对个数；综合题逐题录入，满分以这里为准（主观题分值切题时会尝试读卷面的"(8分)"）。
        </span>
      </div>

      <section class="card">
        <h2>选择题（{{ choice.length }} 组，共 {{ choiceFull }} 分）</h2>
        <div class="struct-row struct-head">
          <span>科目</span><span>起始题号</span><span>结束题号</span><span>每题分值</span><span>小计</span><span></span>
        </div>
        <div v-for="(g, i) in choice" :key="'c' + i" class="struct-row">
          <select v-model="g.subject">
            <option v-for="[key, name] in SUBJECTS" :key="key" :value="key">{{ name }}</option>
          </select>
          <input type="number" min="1" max="60" v-model.number="g.from" />
          <input type="number" min="1" max="60" v-model.number="g.to" />
          <input type="number" min="0.5" max="10" step="0.5" v-model.number="g.per_score" />
          <span class="hint">{{ Math.max(0, g.to - g.from + 1) }} 题 / {{ Math.round(Math.max(0, g.to - g.from + 1) * g.per_score * 10) / 10 }} 分</span>
          <button class="btn tiny danger" @click="choice.splice(i, 1)">删</button>
        </div>
        <button class="btn tiny" @click="addChoice">＋ 加一组</button>
        <p class="hint">
          同一科目可以有多组（比如 DS 1-5 每题 2 分、6-10 每题 3 分），题号不能重叠、也不能和综合题冲突。
        </p>
      </section>

      <section class="card">
        <h2>综合题（{{ subjective.length }} 题，共 {{ subjectiveFull }} 分）</h2>
        <div class="struct-row struct-head">
          <span>题号</span><span>科目</span><span>满分</span><span></span>
        </div>
        <div v-for="(s, i) in subjective" :key="'s' + i" class="struct-row">
          <input type="number" min="1" max="60" v-model.number="s.qno" />
          <select v-model="s.subject">
            <option v-for="[key, name] in SUBJECTS" :key="key" :value="key">{{ name }}</option>
          </select>
          <input type="number" min="0" max="50" step="0.5" v-model.number="s.full" />
          <button class="btn tiny danger" @click="subjective.splice(i, 1)">删</button>
        </div>
        <button class="btn tiny" @click="addSubjective">＋ 加一题</button>
      </section>

      <section class="card">
        <h2>满分合计</h2>
        <div class="totals-line">
          <span v-for="[key, name] in SUBJECTS" :key="key" class="total-chip">
            {{ name }} <b>{{ Math.round(totals.module[key] * 10) / 10 }}</b>
            <span class="hint">/ {{ SUBJECT_FULL[key] }}</span>
          </span>
          <span class="total-chip strong">
            总分 <b>{{ totals.total }}</b>
            <span class="hint" :class="{ warn: totals.total !== 150 }">
              / 150{{ totals.total !== 150 ? "（不是 150，确认没错？）" : "" }}
            </span>
          </span>
        </div>
        <div class="struct-actions">
          <button class="btn primary" :disabled="saving" @click="save">
            {{ saving ? "保存中…" : "保存结构" }}
          </button>
          <button class="btn" :disabled="saving" @click="rebuild">按题目重建</button>
          <button class="btn" :disabled="saving" @click="load">放弃修改</button>
        </div>
        <p class="hint">
          切完题后结构是自动推导的；人工校正过题号/科目/分值后点「按题目重建」。
          用户端的录成绩表单与算分都按这份结构走，**已经存过的成绩不会被重新解释**。
        </p>
      </section>
    </template>
  </div>
</template>
