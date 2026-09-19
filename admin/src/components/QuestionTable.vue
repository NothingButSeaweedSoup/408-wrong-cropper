<script setup>
import { computed, ref } from "vue";
import { api } from "../api.js";

const props = defineProps({
  paper: { type: Object, required: true },
  version: { type: Number, default: 0 },
  activeId: { type: Number, default: 0 },
});
const emit = defineEmits(["refresh", "notify", "select"]);

const SUBJECTS = { ds: "数据结构", co: "计组", os: "操作系统", cn: "计网" };
const busy = ref(0);

const questions = computed(() => props.paper.questions || []);

function thumb(q) {
  return q.image_urls?.[0] ? `${q.image_urls[0]}?v=${props.version}` : "";
}

async function save(q, patch) {
  busy.value = q.id;
  try {
    await api.patchQuestion(q.id, patch);
    emit("refresh", `第 ${q.question_no} 题已保存`);
  } catch (err) {
    emit("notify", `保存失败：${err.message}`, true);
    emit("refresh");
  } finally {
    busy.value = 0;
  }
}

async function mergeUp(index) {
  const current = questions.value[index];
  const previous = questions.value[index - 1];
  if (!previous) return;
  if (current.question_no !== previous.question_no && !confirm(
    `第 ${current.question_no} 题的题块将并入第 ${previous.question_no} 题，继续？`
  )) return;
  try {
    await api.merge(props.paper.id, previous.id, current.id);
    emit("refresh", `已合并到第 ${previous.question_no} 题`);
  } catch (err) {
    emit("notify", `合并失败：${err.message}`, true);
  }
}

async function remove(q) {
  if (!confirm(`删除第 ${q.question_no} 题？`)) return;
  try {
    await api.deleteQuestion(q.id);
    emit("refresh", "已删除");
  } catch (err) {
    emit("notify", `删除失败：${err.message}`, true);
  }
}
</script>

<template>
  <div class="qtable">
    <div class="qtable-head">
      <span>共 {{ questions.length }} 题</span>
      <span class="hint">改题号/科目会立即生效，图片不变；改完可点"整卷重裁"</span>
    </div>
    <div
      v-for="(q, index) in questions"
      :key="q.id"
      class="qrow"
      :class="{ active: q.id === activeId }"
      @click="emit('select', q)"
    >
      <img v-if="thumb(q)" :src="thumb(q)" loading="lazy" alt="" />
      <div class="qbody">
        <div class="qline">
          <input
            class="no"
            type="number"
            :value="q.question_no"
            :disabled="busy === q.id"
            @change="save(q, { question_no: Number($event.target.value) })"
          />
          <select :value="q.subject" @change="save(q, { subject: $event.target.value })">
            <option v-for="(name, key) in SUBJECTS" :key="key" :value="key">{{ name }}</option>
          </select>
          <select :value="q.type" @change="save(q, { type: $event.target.value })">
            <option value="choice">选择</option>
            <option value="subjective">主观</option>
          </select>
          <!-- 分值：主观题每题不同，改完去「试卷结构」点一次「按题目重建」 -->
          <input
            class="score"
            type="number"
            step="0.5"
            min="0"
            max="50"
            title="这题的分值（主观题用；改完在「试卷结构」里重建）"
            :value="q.score ?? ''"
            :disabled="busy === q.id"
            @change="save(q, { score: $event.target.value === '' ? 0 : Number($event.target.value) })"
          />
          <span class="tag">{{ q.image_urls.length }} 图</span>
          <span class="tag">{{ q.height_cm }} cm</span>
          <span v-if="q.source === 'manual'" class="tag manual">已校正</span>
        </div>
        <div class="qline small">
          <span v-for="b in q.bbox.blocks" :key="b.page_no" class="tag">
            p{{ b.page_no }}: {{ b.y0 }}~{{ b.y1 }}
          </span>
          <span v-if="q.bbox.sub_marks?.length" class="tag">
            小问 {{ q.bbox.sub_marks.length }} 处
          </span>
        </div>
      </div>
      <div class="qacts">
        <button class="btn tiny" :disabled="index === 0" @click.stop="mergeUp(index)">并入上一题</button>
        <button class="btn tiny danger" @click.stop="remove(q)">删除</button>
      </div>
    </div>
  </div>
</template>
