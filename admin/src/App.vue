<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api, clearAuth, getAdminKey, getUserToken, setAdminKey, setUserToken } from "./api.js";
import PageEditor from "./components/PageEditor.vue";
import QuestionTable from "./components/QuestionTable.vue";

/* ---------------------------------------------------------- 登录门 */
const locked = ref(true);
const mode = ref("user"); // user = 用管理员账号登录；key = 兜底密钥
const checking = ref(false);
const authError = ref("");
const loginUser = ref("");
const loginPass = ref("");
const keyInput = ref("");
const identity = ref(null); // {via: "user"|"key", user: {...}}

const papers = ref([]);
const paper = ref(null);
const page = ref(1);
const version = ref(0);
const uploadYear = ref(new Date().getFullYear() - 17); // 408 真题一般从 2009 起
const uploadTitle = ref("");
const fileInput = ref(null);
const busy = ref(false);
const toast = ref({ text: "", error: false, visible: false });
const settings = ref({ allow_register: true, user_count: 0 });
const users = ref([]);
const newUser = ref({ username: "", password: "", display_name: "", is_admin: false });

let pollTimer = null;

const processing = computed(() =>
  paper.value ? ["uploaded", "rendering", "ocr", "splitting"].includes(paper.value.status) : false
);
const whoami = computed(() => {
  if (!identity.value) return "";
  if (identity.value.via === "key") return "兜底密钥登录";
  const user = identity.value.user || {};
  return `管理员：${user.display_name || user.username}`;
});

function notify(text, error = false) {
  toast.value = { text, error, visible: true };
  clearTimeout(notify._timer);
  notify._timer = setTimeout(() => (toast.value.visible = false), 2800);
}

function switchMode(next) {
  mode.value = next;
  authError.value = "";
}

async function finishUnlock() {
  identity.value = await api.verify();
  locked.value = false;
  loginPass.value = "";
  keyInput.value = "";
  authError.value = "";
  await bootstrap();
}

async function unlockByUser() {
  if (!loginUser.value || !loginPass.value) return;
  checking.value = true;
  authError.value = "";
  try {
    const data = await api.login(loginUser.value.trim(), loginPass.value);
    setUserToken(data.token);
    try {
      await finishUnlock();
    } catch (err) {
      // 账号密码对，但不是管理员：把提示说清楚，并保持登录态（用户端还能用）
      authError.value =
        err.status === 403
          ? `「${data.user.display_name || data.user.username}」不是管理员。` +
            `请在服务器 backend/.env 的 ZC_ADMIN_USERS 里加上这个用户名，或换管理员账号登录。`
          : `验证失败：${err.message}`;
    }
  } catch (err) {
    authError.value = err.status === 401 ? "用户名或密码不正确" : `登录失败：${err.message}`;
  } finally {
    checking.value = false;
  }
}

async function unlockByKey() {
  if (!keyInput.value) return;
  checking.value = true;
  authError.value = "";
  try {
    setAdminKey(keyInput.value.trim());
    await finishUnlock();
  } catch (err) {
    setAdminKey("");
    authError.value = "密钥不对，请重新输入";
  } finally {
    checking.value = false;
  }
}

async function lock() {
  clearAuth();
  identity.value = null;
  locked.value = true;
  paper.value = null;
  papers.value = [];
  users.value = [];
  clearTimeout(pollTimer);
}

function onLocked() {
  clearTimeout(pollTimer);
  identity.value = null;
  locked.value = true;
  authError.value = "登录态已失效，请重新登录";
}

/* ---------------------------------------------------------- 数据 */
async function bootstrap() {
  await loadAdminInfo();
  await loadPapers();
  if (papers.value.length) await selectPaper(papers.value[0]);
  else paper.value = null;
}

async function loadAdminInfo() {
  try {
    settings.value = await api.getSettings();
    users.value = await api.listUsers();
  } catch (err) {
    notify(`用户信息加载失败：${err.message}`, true);
  }
}

async function toggleRegister() {
  try {
    settings.value = await api.setSettings({ allow_register: !settings.value.allow_register });
    notify(settings.value.allow_register ? "已允许用户自助注册" : "已关闭自助注册");
  } catch (err) {
    notify(`操作失败：${err.message}`, true);
  }
}

async function addUser() {
  const { username, password, display_name, is_admin } = newUser.value;
  if (!username || !password) return notify("用户名和密码都要填", true);
  try {
    await api.createUser({ username, password, display_name, is_admin });
    newUser.value = { username: "", password: "", display_name: "", is_admin: false };
    await loadAdminInfo();
    notify("已创建用户");
  } catch (err) {
    notify(`创建失败：${err.message}`, true);
  }
}

async function toggleAdmin(user) {
  try {
    await api.patchUser(user.id, { is_admin: !user.is_admin });
    await loadAdminInfo();
    notify(`${user.display_name} ${user.is_admin ? "已取消管理员" : "已设为管理员"}`);
  } catch (err) {
    notify(`操作失败：${err.message}`, true);
  }
}

async function removeUser(user) {
  if (!confirm(`删除用户「${user.display_name}」及其 ${user.record_count} 条得分记录？`)) return;
  try {
    await api.deleteUser(user.id);
    await loadAdminInfo();
    notify("已删除");
  } catch (err) {
    notify(`删除失败：${err.message}`, true);
  }
}

async function loadPapers() {
  papers.value = await api.listPapers();
}

async function loadPaper(id, keepPage = false) {
  paper.value = await api.getPaper(id);
  version.value += 1;
  if (!keepPage) page.value = 1;
  if (paper.value.pages.length && page.value > paper.value.pages.length) page.value = 1;
  if (processing.value) schedulePoll();
}

async function refresh(message) {
  if (paper.value) await loadPaper(paper.value.id, true);
  await loadPapers();
  if (message) notify(message);
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!paper.value || locked.value) return;
    try {
      paper.value = await api.getPaper(paper.value.id);
      version.value += 1;
      if (processing.value) schedulePoll();
      else {
        await loadPapers();
        notify(paper.value.message || "处理完成");
      }
    } catch (err) {
      notify(`状态查询失败：${err.message}`, true);
    }
  }, 1500);
}

async function selectPaper(item) {
  try {
    page.value = 1;
    await loadPaper(item.id);
  } catch (err) {
    notify(`加载失败：${err.message}`, true);
  }
}

async function doUpload() {
  const file = fileInput.value?.files?.[0];
  if (!file) return notify("请选择 PDF 文件", true);
  busy.value = true;
  try {
    const created = await api.upload(file, uploadYear.value, uploadTitle.value);
    await loadPapers();
    await selectPaper(created);
    notify("已上传，正在渲染 + OCR，请稍候…");
  } catch (err) {
    notify(`上传失败：${err.message}`, true);
  } finally {
    busy.value = false;
    if (fileInput.value) fileInput.value.value = "";
  }
}

async function reprocess() {
  if (!paper.value || !confirm("重新识别会覆盖当前所有校正结果，继续？")) return;
  await api.reprocess(paper.value.id);
  notify("已重新排队处理");
  schedulePoll();
}

async function recrop() {
  if (!paper.value) return;
  const res = await api.recrop(paper.value.id);
  await refresh(`已重裁 ${res.count} 道题`);
}

async function removePaper() {
  if (!paper.value || !confirm(`删除《${paper.value.title}》及其所有题目图片？`)) return;
  await api.deletePaper(paper.value.id);
  paper.value = null;
  await loadPapers();
  notify("已删除");
}

onMounted(async () => {
  window.addEventListener("zc:admin-locked", onLocked);
  if (getUserToken() || getAdminKey()) {
    try {
      await finishUnlock();
      return;
    } catch (err) {
      if (err.status === 403) {
        authError.value = "当前浏览器登录的账号不是管理员，请用管理员账号登录";
      }
      locked.value = true;
    }
  }
  locked.value = true;
});

onUnmounted(() => {
  window.removeEventListener("zc:admin-locked", onLocked);
  clearTimeout(pollTimer);
});
</script>

<template>
  <!-- 登录门：默认用管理员账号，兜底可用密钥 -->
  <div v-if="locked" class="lock">
    <div class="lock-card">
      <h1>408 错题助手 · 管理后台</h1>

      <template v-if="mode === 'user'">
        <p class="hint">
          用管理员账号登录。管理员由服务器 <code>backend/.env</code> 的
          <code>ZC_ADMIN_USERS</code> 指定（用户名），启动时会打印当前名单。
        </p>
        <input v-model="loginUser" placeholder="用户名" autocomplete="username" />
        <input
          v-model="loginPass"
          type="password"
          placeholder="密码"
          autocomplete="current-password"
          @keyup.enter="unlockByUser"
        />
        <button class="btn primary" :disabled="checking || !loginUser || !loginPass" @click="unlockByUser">
          {{ checking ? "登录中…" : "登录" }}
        </button>
        <p v-if="authError" class="error">{{ authError }}</p>
        <p class="hint">
          还没有账号？先在 <a href="/" target="_blank" rel="noopener">用户端</a> 注册，
          再把用户名加进 <code>ZC_ADMIN_USERS</code> 重启后端即可。
        </p>
        <button class="btn link-btn" @click="switchMode('key')">用兜底密钥登录 →</button>
      </template>

      <template v-else>
        <p class="hint">
          兜底密钥在服务器 <code>backend/.env</code> 的 <code>ZC_ADMIN_KEY</code>
          （首次启动自动生成并打印）。脚本调用、以及还没有管理员账号时用它进来建账号。
        </p>
        <input
          v-model="keyInput"
          type="password"
          placeholder="管理员密钥"
          @keyup.enter="unlockByKey"
        />
        <button class="btn primary" :disabled="checking || !keyInput" @click="unlockByKey">
          {{ checking ? "验证中…" : "进入" }}
        </button>
        <p v-if="authError" class="error">{{ authError }}</p>
        <button class="btn link-btn" @click="switchMode('user')">← 用账号登录</button>
      </template>

      <a class="link" href="/" target="_blank" rel="noopener">我只是来看用户端 →</a>
    </div>
  </div>

  <div v-else class="app">
    <header class="app-header">
      <h1>408 错题助手 · 管理后台</h1>
      <span class="hint">上传真题 PDF → 自动切题 → 人工校正</span>
      <span class="spacer"></span>
      <span class="whoami">{{ whoami }}</span>
      <a class="link" href="/" target="_blank" rel="noopener">用户端 →</a>
      <button class="btn" @click="lock">退出</button>
    </header>

    <aside class="sidebar">
      <section class="card">
        <h2>导入真题</h2>
        <label>年份<input type="number" v-model.number="uploadYear" min="1990" max="2100" /></label>
        <label>标题（可选）<input type="text" v-model="uploadTitle" placeholder="2009 年 408 真题" /></label>
        <input ref="fileInput" type="file" accept="application/pdf" />
        <button class="btn primary" :disabled="busy" @click="doUpload">
          {{ busy ? "上传中…" : "上传并自动切题" }}
        </button>
        <p class="hint">尽量用清晰的扫描件；一次几十页需要 1~3 分钟 OCR。</p>
      </section>

      <section class="card">
        <h2>真题库（{{ papers.length }}）</h2>
        <ul class="paper-list">
          <li
            v-for="item in papers"
            :key="item.id"
            :class="{ active: paper && paper.id === item.id }"
            @click="selectPaper(item)"
          >
            <div class="title">{{ item.title || item.year + " 年 408 真题" }}</div>
            <div class="meta">
              {{ item.question_count }} 题 · {{ item.page_count }} 页
              <span class="status" :data-status="item.status">{{ item.status }}</span>
            </div>
          </li>
        </ul>
        <p v-if="!papers.length" class="hint">还没有真题，先上传一份 PDF。</p>
      </section>

      <section class="card">
        <h2>用户（{{ users.length }}）</h2>
        <label class="check-line">
          <input type="checkbox" :checked="settings.allow_register" @change="toggleRegister" />
          允许用户自助注册
        </label>
        <div class="user-new">
          <input v-model="newUser.username" placeholder="用户名" />
          <input v-model="newUser.password" type="password" placeholder="密码（≥6 位）" />
          <input v-model="newUser.display_name" placeholder="昵称（可选）" />
          <label class="check-line">
            <input type="checkbox" v-model="newUser.is_admin" />
            设为管理员
          </label>
          <button class="btn tiny" @click="addUser">新建用户</button>
        </div>
        <ul class="user-list">
          <li v-for="u in users" :key="u.id">
            <label class="check-line grow">
              <input type="checkbox" :checked="u.is_admin" @change="toggleAdmin(u)" />
              <span class="uname">{{ u.display_name }}</span>
            </label>
            <span class="hint">{{ u.record_count }} 条</span>
            <button class="btn tiny danger" @click="removeUser(u)">删除</button>
          </li>
        </ul>
        <p v-if="!users.length" class="hint">还没有用户；关掉自助注册后在这里给用户开账号。</p>
        <p class="hint">
          勾选＝管理员，可进本后台。若某人在 <code>ZC_ADMIN_USERS</code> 里，后端每次启动会重新勾上。
        </p>
      </section>
    </aside>

    <main class="workspace">
      <div v-if="!paper" class="placeholder">左侧选择或导入一份真题</div>
      <template v-else>
        <div class="paper-toolbar">
          <strong>{{ paper.title || paper.year + " 年 408 真题" }}</strong>
          <span class="status" :data-status="paper.status">{{ paper.status }}</span>
          <span v-if="processing" class="progress">
            {{ paper.progress }}% · {{ paper.message }}
          </span>
          <span v-else-if="paper.message" class="hint">{{ paper.message }}</span>
          <span class="spacer"></span>
          <button class="btn" @click="recrop">整卷重裁</button>
          <button class="btn" @click="reprocess">重新识别</button>
          <button class="btn danger" @click="removePaper">删除真题</button>
        </div>

        <div v-if="paper.pages.length" class="panes">
          <PageEditor
            :paper="paper"
            :version="version"
            v-model:page="page"
            @refresh="refresh"
            @notify="notify"
          />
          <QuestionTable
            :paper="paper"
            :version="version"
            @refresh="refresh"
            @notify="notify"
            @select="(q) => (page = q.bbox.blocks?.[0]?.page_no || 1)"
          />
        </div>
        <div v-else class="placeholder">
          {{ processing ? "正在渲染页面…" : "没有页面图，点「重新识别」重跑流程" }}
        </div>
      </template>
    </main>
  </div>

  <div v-if="toast.visible" class="toast" :class="{ error: toast.error }">{{ toast.text }}</div>
</template>
