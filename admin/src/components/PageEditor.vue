<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "../api.js";

const props = defineProps({
  paper: { type: Object, required: true },
  page: { type: Number, required: true },
  version: { type: Number, default: 0 },
});
const emit = defineEmits(["refresh", "notify"]);

const SUBJECT_COLORS = { ds: "#2f6fed", co: "#22a06b", os: "#e08600", cn: "#8b5cf6" };

const imgEl = ref(null);
const scale = ref(1);
const natural = ref({ w: 1, h: 1 });
const linkBoundaries = ref(true);
const drafts = ref({}); // qid -> {y0, y1} 拖动中的实时草稿
const drag = ref(null);
const cut = ref(null); // { qid, qno, y }

const pageUrl = computed(
  () => `/api/papers/${props.paper.id}/pages/${props.page}?v=${props.version}`
);
const naturalHeight = computed(() => natural.value.h);

const bands = computed(() => {
  const out = [];
  for (const q of props.paper.questions || []) {
    const block = (q.bbox?.blocks || []).find((b) => b.page_no === props.page);
    if (!block) continue;
    const draft = drafts.value[q.id] || block;
    out.push({
      qid: q.id,
      qno: q.question_no,
      subject: q.subject,
      type: q.type,
      y0: draft.y0,
      y1: draft.y1,
      color: SUBJECT_COLORS[q.subject] || "#888",
      crossPage: (q.bbox?.blocks || []).length > 1,
    });
  }
  return out.sort((a, b) => a.y0 - b.y0);
});

const cutStyle = computed(() => {
  if (!cut.value) return null;
  return { top: `${cut.value.y * scale.value}px`, background: "#e5484d" };
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function measure() {
  const el = imgEl.value;
  if (!el || !el.naturalWidth) return;
  natural.value = { w: el.naturalWidth, h: el.naturalHeight };
  scale.value = el.clientWidth / el.naturalWidth;
}

function bandStyle(band) {
  return {
    top: `${band.y0 * scale.value}px`,
    height: `${Math.max(4, (band.y1 - band.y0) * scale.value)}px`,
    borderColor: band.color,
    background: `${band.color}14`,
  };
}

function startDrag(event, band, edge) {
  event.preventDefault();
  event.stopPropagation();
  drag.value = { ...band, edge, startY: event.clientY };
  drafts.value = { ...drafts.value, [band.qid]: { y0: band.y0, y1: band.y1 } };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

function onMove(event) {
  const d = drag.value;
  if (!d) return;
  const dy = (event.clientY - d.startY) / scale.value;
  const next = { y0: d.y0, y1: d.y1 };
  if (d.edge === "y0") next.y0 = clamp(Math.round(d.y0 + dy), 0, d.y1 - 20);
  else next.y1 = clamp(Math.round(d.y1 + dy), d.y0 + 20, naturalHeight.value);
  drafts.value = { ...drafts.value, [d.qid]: next };
}

async function onUp() {
  const d = drag.value;
  drag.value = null;
  window.removeEventListener("pointermove", onMove);
  window.removeEventListener("pointerup", onUp);
  if (!d) return;
  const draft = drafts.value[d.qid];
  if (!draft || (draft.y0 === d.y0 && draft.y1 === d.y1)) return;
  try {
    await api.patchQuestion(d.qid, {
      block: { page_no: props.page, y0: draft.y0, y1: draft.y1 },
    });
    // 联动：拖动上边界时把上一题的下边界一起挪，避免出现重叠或空洞
    if (linkBoundaries.value) {
      const siblings = bands.value.filter((b) => b.qid !== d.qid);
      const neighbour =
        d.edge === "y0"
          ? siblings.filter((b) => b.y1 <= d.y0).sort((a, b) => b.y1 - a.y1)[0]
          : siblings.filter((b) => b.y0 >= d.y1).sort((a, b) => a.y0 - b.y0)[0];
      if (neighbour) {
        const patch =
          d.edge === "y0"
            ? { page_no: props.page, y0: neighbour.y0, y1: draft.y0 }
            : { page_no: props.page, y0: draft.y1, y1: neighbour.y1 };
        if (patch.y1 - patch.y0 >= 20) await api.patchQuestion(neighbour.qid, { block: patch });
      }
    }
    emit("refresh", `已保存第 ${d.qno} 题边界`);
  } catch (err) {
    emit("notify", `保存失败：${err.message}`, true);
    emit("refresh");
  }
}

function pickCut(event, band) {
  const rect = event.currentTarget.getBoundingClientRect();
  const y = clamp(Math.round((event.clientY - rect.top) / scale.value + band.y0), band.y0 + 20, band.y1 - 20);
  cut.value = { qid: band.qid, qno: band.qno, y };
}

async function applySplit() {
  if (!cut.value) return;
  try {
    await api.split(props.paper.id, props.page, cut.value.y);
    emit("refresh", `已从第 ${cut.value.qno} 题切出新题`);
    cut.value = null;
  } catch (err) {
    emit("notify", `切分失败：${err.message}`, true);
  }
}

function gotoPage(no) {
  const max = props.paper.pages.length;
  const next = clamp(no, 1, max);
  if (next === props.page) return;
  drafts.value = {};
  cut.value = null;
  emit("update:page", next);
}

watch(
  () => [props.page, props.paper.id],
  () => {
    drafts.value = {};
    cut.value = null;
    nextTick(measure);
  }
);
watch(
  () => props.version,
  () => nextTick(measure)
);

onMounted(() => {
  window.addEventListener("resize", measure);
  measure();
});
onBeforeUnmount(() => {
  window.removeEventListener("resize", measure);
  window.removeEventListener("pointermove", onMove);
  window.removeEventListener("pointerup", onUp);
});
</script>

<template>
  <div class="editor">
    <div class="toolbar">
      <button class="btn" @click="gotoPage(page - 1)" :disabled="page <= 1">上一页</button>
      <span class="page-info">第 {{ page }} / {{ paper.pages.length }} 页</span>
      <button class="btn" @click="gotoPage(page + 1)" :disabled="page >= paper.pages.length">
        下一页
      </button>
      <label class="check"><input type="checkbox" v-model="linkBoundaries" /> 相邻题边界联动</label>
      <span class="hint">拖动色块上下边缘即改边界；点色块选切分位置</span>
      <button v-if="cut" class="btn danger" @click="applySplit">
        ✂ 在第 {{ cut.qno }} 题 y={{ cut.y }} 处切分
      </button>
    </div>

    <div class="canvas-wrap">
      <img ref="imgEl" :src="pageUrl" alt="page" @load="measure" />
      <div class="overlay">
        <div
          v-for="band in bands"
          :key="band.qid"
          class="band"
          :style="bandStyle(band)"
          @click="pickCut($event, band)"
        >
          <span class="band-label" :style="{ background: band.color }">
            {{ band.qno }} · {{ band.y0 }}~{{ band.y1 }}
            <template v-if="band.crossPage"> ⤵跨页</template>
          </span>
          <div class="handle top" @pointerdown="startDrag($event, band, 'y0')"></div>
          <div class="handle bottom" @pointerdown="startDrag($event, band, 'y1')"></div>
        </div>
        <div v-if="cutStyle" class="cut-line" :style="cutStyle"></div>
      </div>
    </div>
  </div>
</template>
