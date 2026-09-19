/* 前端冒烟测试：用迷你 DOM 在 Node 里跑 core.js / app.js / scores.js / chart.js。
 *
 * 为什么需要它：浏览器里的交互（Tab 切换、登录门、录入表单）以前没有任何自动化覆盖，
 * 结果“Tab 点击事件忘了绑定”这种错就漏到了用户手上。这个脚本用假 DOM + 假 fetch
 * 把真实的前端脚本跑起来，断言关键交互真的接上了。
 *
 * 跑法：node tests/web_dom_smoke.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const WEB = path.join(ROOT, "web");

let ok = true;
const check = (label, condition, detail = "") => {
  ok = ok && Boolean(condition);
  console.log(`  [${condition ? "PASS" : "FAIL"}] ${label}${detail ? ` — ${detail}` : ""}`);
};

/* ---------------------------------------------------------------- 迷你 DOM */
class Node {}

class ClassList {
  constructor(el) {
    this.el = el;
    this.set = new Set();
  }
  _sync() {
    this.el._className = [...this.set].join(" ");
  }
  add(...names) {
    names.forEach((n) => this.set.add(n));
    this._sync();
  }
  remove(...names) {
    names.forEach((n) => this.set.delete(n));
    this._sync();
  }
  contains(name) {
    return this.set.has(name);
  }
  toggle(name, force) {
    const want = force === undefined ? !this.set.has(name) : force;
    if (want) this.set.add(name);
    else this.set.delete(name);
    this._sync();
    return want;
  }
}

class Element extends Node {
  constructor(tag) {
    super();
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this._text = "";
    this._html = "";
    this.classList = new ClassList(this);
    this._className = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.files = [];
  }
  get id() {
    return this.attributes.id || "";
  }
  set id(value) {
    this.attributes.id = String(value);
  }
  get className() {
    return this._className;
  }
  set className(value) {
    this._className = value || "";
    this.classList.set = new Set(String(value || "").split(/\s+/).filter(Boolean));
  }
  get textContent() {
    return this._text;
  }
  set textContent(value) {
    this._text = String(value);
  }
  get innerHTML() {
    return this._html;
  }
  set innerHTML(value) {
    this._html = String(value);
    this.children = [];
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = value;
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(name) {
    return this.attributes[name] ?? null;
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  }
  appendChild(child) {
    if (child instanceof DocumentFragment) {
      // 真实 DOM 里 append(fragment) 会把片段的孩子搬进来
      child.children.forEach((c) => this.children.push(c));
      child.children = [];
      return child;
    }
    this.children.push(child);
    return child;
  }
  append(...nodes) {
    nodes.forEach((n) => this.appendChild(n));
  }
  remove() {
    /* 仅用于清理，测试里不需要真的摘除 */
  }
  insertAdjacentHTML() {
    /* 卡片上的角标/标签是拼 HTML 塞进去的，测试只关心元素数量，这里当空实现 */
  }
  scrollIntoView() {}
  click() {
    if (typeof this.onclick === "function") this.onclick({ target: this, preventDefault() {}, stopPropagation() {} });
    (this.listeners.click || []).forEach((fn) => fn({ target: this, preventDefault() {}, stopPropagation() {} }));
  }
  getBoundingClientRect() {
    return { top: 0, left: 0, width: 720, height: 360 };
  }
  get classListNames() {
    return [...this.classList.set];
  }
}

class TextNode extends Node {
  constructor(text) {
    super();
    this.textContent = String(text);
  }
}

class DocumentFragment extends Node {
  constructor() {
    super();
    this.children = [];
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  append(...nodes) {
    nodes.forEach((n) => this.appendChild(n));
  }
}

/* 只认 #id 和 .class 两种选择器（前端确实只用这两种），
   所以从 index.html 里平铺抽取「标签 + id + class + data-*」就够了，不必写完整 HTML 解析器。 */
function buildDocument(htmlPath) {
  const html = fs.readFileSync(htmlPath, "utf8");
  const byId = {};
  const byClass = {};
  const all = [];
  const tagRe = /<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)\/?>/g;
  let match;
  while ((match = tagRe.exec(html))) {
    const el = new Element(match[1]);
    const attrRe = /([\w:-]+)\s*=\s*"([^"]*)"/g;
    let attr;
    while ((attr = attrRe.exec(match[2]))) el.setAttribute(attr[1], attr[2]);
    all.push(el);
    if (el.attributes.id) byId[el.attributes.id] = el;
    for (const cls of el.className.split(/\s+/).filter(Boolean)) {
      (byClass[cls] = byClass[cls] || []).push(el);
    }
  }
  const document = {
    readyState: "complete",
    body: new Element("body"),
    documentElement: new Element("html"),
    querySelector(sel) {
      if (sel.startsWith("#")) return byId[sel.slice(1)] || null;
      if (sel.startsWith(".")) return (byClass[sel.slice(1)] || [])[0] || null;
      return null;
    },
    querySelectorAll(sel) {
      if (sel.startsWith(".")) return byClass[sel.slice(1)] || [];
      if (sel.startsWith("#")) return byId[sel.slice(1)] ? [byId[sel.slice(1)]] : [];
      return [];
    },
    createElement(tag) {
      return new Element(tag);
    },
    createElementNS(_ns, tag) {
      return new Element(tag);
    },
    createTextNode(text) {
      return new TextNode(text);
    },
    createDocumentFragment() {
      return new DocumentFragment();
    },
  };
  return { document, byId, byClass, all };
}

/* ---------------------------------------------------------------- 假 fetch */
function makeFetch(state) {
  const calls = [];
  const json = (body, status = 200) => ({
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  return {
    calls,
    fetch: async (url, options = {}) => {
      const method = (options.method || "GET").toUpperCase();
      calls.push(`${method} ${url}`);
      const [pathOnly] = String(url).split("?");
      if (pathOnly === "/api/auth/me") {
        return state.loggedIn
          ? json({ user: state.user, allow_register: true })
          : json({ detail: "请先登录" }, 401);
      }
      if (pathOnly === "/api/auth/login" || pathOnly === "/api/auth/register") {
        state.loggedIn = true;
        return json({ token: "fake-token", user: state.user, expires_at: "2027-01-01 00:00:00" });
      }
      if (pathOnly === "/api/auth/status") return json({ allow_register: true, user_count: 1 });
      if (pathOnly === "/api/catalog") return json({ years: [{ year: 2009, papers: 1, questions: 2 }] });
      if (pathOnly === "/api/questions") {
        return json([
          { id: 1, year: 2009, question_no: 1, subject: "ds", type: "choice", image_urls: ["/api/questions/1/images/0"] },
        ]);
      }
      if (pathOnly === "/api/scores/schema") return json(state.schema);
      if (pathOnly === "/api/scores") {
        return method === "POST" ? json({ total_score: 112, total_full: 150 }) : json(state.records);
      }
      if (pathOnly === "/api/scores/trend") return json(state.trend);
      if (pathOnly === "/api/exports") return json([]);
      return json({ detail: `未在假 fetch 里实现: ${method} ${pathOnly}` }, 404);
    },
  };
}

/* ---------------------------------------------------------------- 跑起来 */
async function main() {
  const { document, byId, byClass } = buildDocument(path.join(WEB, "index.html"));

  const state = {
    loggedIn: false,
    user: { id: 1, username: "tester", display_name: "测试", is_admin: false },
    schema: {
      choice_groups: [
        { subject: "ds", name: "数据结构", from: 1, to: 11, count: 11, per_score: 2, full: 22 },
        { subject: "co", name: "计算机组成原理", from: 12, to: 22, count: 11, per_score: 2, full: 22 },
        { subject: "os", name: "操作系统", from: 23, to: 32, count: 10, per_score: 2, full: 20 },
        { subject: "cn", name: "计算机网络", from: 33, to: 40, count: 8, per_score: 2, full: 16 },
      ],
      subjective: [{ qno: 41, subject: "ds", name: "数据结构", full: 10 }],
      module_full: { ds: 45, co: 45, os: 35, cn: 25, total: 150 },
    },
    records: [
      {
        id: 1, paper_year: 2009, practice_date: "2026-09-19", total_score: 112, total_full: 150, total_rate: 74.7,
        ds: 36, co: 32, os: 25, cn: 19, note: "第一次", rates: { ds: 80, co: 71.1, os: 71.4, cn: 76 },
      },
    ],
    trend: {
      x_axis: "practice_date", aggregate: "latest", labels: ["2026-09-19"],
      series: { ds: [80], co: [71.1], os: [71.4], cn: [76], total: [112] },
      points: [{ label: "2026-09-19", year: 2009, total_score: 112 }],
      module_full: { ds: 45, co: 45, os: 35, cn: 25, total: 150 },
      empty: false,
      estimate: {
        count: 1, weights: [1], base_weights: [0.5], modules: { ds: 80, co: 71.1, os: 71.4, cn: 76 },
        total: 112, total_full: 150, total_rate: 74.7, samples: [{ practice_date: "2026-09-19", paper_year: 2009, total_score: 112, weight: 1 }],
        message: "只有 1 次记录，权重已按比例归一化",
      },
    },
  };

  const storage = new Map();
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Promise,
    JSON,
    Math,
    Date,
    Number,
    String,
    Boolean,
    Array,
    Object,
    RegExp,
    Error,
    FormData: class FormData {}, // api() 里会做 instanceof FormData 判断
    confirm: () => true,
    alert: () => {},
    location: { href: "", reload: () => {} },
    CustomEvent: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    document,
    Node,
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
    },
  };
  sandbox.window = sandbox;
  sandbox.window.addEventListener = () => {};
  sandbox.window.dispatchEvent = () => true;

  const fake = makeFetch(state);
  sandbox.fetch = fake.fetch;

  vm.createContext(sandbox);
  for (const file of ["core.js", "chart.js", "app.js", "scores.js"]) {
    vm.runInContext(fs.readFileSync(path.join(WEB, file), "utf8"), sandbox, { filename: file });
  }
  const ZC = sandbox.ZC;
  const tick = () => new Promise((resolve) => setTimeout(resolve, 30));

  console.log("1) 未登录时应显示登录门");
  await tick();
  await tick();
  check("#gate 可见", !byId.gate.classList.contains("hidden"));
  check("#shell 隐藏", byId.shell.classList.contains("hidden"));

  console.log("\n2) 填账号密码登录后应进入主界面");
  byId.authUser.value = "tester";
  byId.authPass.value = "secret123";
  byId.authSubmit.click();
  await tick();
  await tick();
  check("登录后 #gate 隐藏", byId.gate.classList.contains("hidden"));
  check("登录后 #shell 显示", !byId.shell.classList.contains("hidden"));
  check("登录后取了目录 /api/catalog", fake.calls.includes("GET /api/catalog"), fake.calls.join(" | "));
  check("登录后取了题目 /api/questions", fake.calls.includes("GET /api/questions"));
  check("选错题页渲染出卡片", byId.questionList.children.length > 0, `${byId.questionList.children.length} 个`);

  console.log("\n3) 点击「得分」Tab（之前就是这里没反应）");
  const scoreTab = byClass.tab.find((t) => t.dataset.view === "scores");
  check("找得到 得分 Tab 按钮", Boolean(scoreTab));
  scoreTab.click();
  await tick();
  await tick();
  check("#view-scores 显示", !byId["view-scores"].classList.contains("hidden"));
  check("#view-pick 隐藏", byId["view-pick"].classList.contains("hidden"));
  check("底部「生成 Word」条隐藏", byClass.bottombar[0].classList.contains("hidden"));
  check("Tab 高亮切到「得分」", scoreTab.classList.contains("active") && !byClass.tab.find((t) => t.dataset.view === "pick").classList.contains("active"));
  check("拉了成绩列表 /api/scores", fake.calls.includes("GET /api/scores"), fake.calls.join(" | "));
  check("拉了趋势 /api/scores/trend", fake.calls.some((c) => c.startsWith("GET /api/scores/trend")));
  check("渲染了成绩记录", byId.recordList.children.length > 0, `${byId.recordList.children.length} 条`);
  check("渲染了当前水平估计", byId.estimate.children.length > 0);
  check("渲染了趋势图", byId.chart.children.length > 0);
  check("顶栏显示了用户名", String(byId.whoami.textContent).includes("测试"), String(byId.whoami.textContent));

  console.log("\n4) 点「录入成绩」应展开表单");
  byId.newRecord.click();
  await tick();
  check("#recordForm 展开", !byId.recordForm.classList.contains("hidden"));
  check("表单里生成了控件", byId.recordForm.children.length > 3, `${byId.recordForm.children.length} 个`);

  console.log("\n5) 点击「导出记录」Tab");
  const historyTab = byClass.tab.find((t) => t.dataset.view === "history");
  historyTab.click();
  await tick();
  check("#view-history 显示", !byId["view-history"].classList.contains("hidden"));
  check("拉了导出历史 /api/exports", fake.calls.includes("GET /api/exports"));

  console.log("\n6) 切回「选错题」Tab");
  byClass.tab.find((t) => t.dataset.view === "pick").click();
  await tick();
  check("#view-pick 又显示出来", !byId["view-pick"].classList.contains("hidden"));
  check("#view-scores 隐藏", byId["view-scores"].classList.contains("hidden"));
  check("ZC 上挂好了所需 API", ["api", "session", "onShow", "onEnter", "chart"].every((k) => k in ZC));

  console.log(ok ? "\n全部通过" : "\n存在失败项");
  process.exit(ok ? 0 : 1);
}

main().catch((err) => {
  console.error("测试自身出错:", err);
  process.exit(1);
});
