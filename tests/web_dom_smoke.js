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
    // 真浏览器里 <input value="10"> 的 **属性** 会反映到 .value **属性值** 上（dirty flag 未置位时）。
    // 迷你 DOM 不镜像的话，前端 el("input", {value: 10}) 建出来的框 .value 会是 ""，
    // 于是 recalc() 读到 0，出现"满分明明是 10，小计却是 0"这种假失败。
    if (name === "value") this.value = String(value);
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
  /** 触发某个事件（迷你 DOM 只做前端真正用到的那几种） */
  fire(type) {
    const event = { target: this, preventDefault() {}, stopPropagation() {} };
    const handler = this[`on${type}`];
    if (typeof handler === "function") handler(event);
    (this.listeners[type] || []).forEach((fn) => fn(event));
  }
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
      if (pathOnly === "/api/catalog")
        return json({ years: state.catalogYears || [{ year: 2009, papers: 1, questions: 2 }] });
      if (pathOnly === "/api/questions") {
        const query = String(url).split("?")[1] || "";
        const params = new URLSearchParams(query);
        let all = state.questions || [
          { id: 1, year: 2009, question_no: 1, subject: "ds", type: "choice", image_urls: ["/api/questions/1/images/0"] },
        ];
        const year = params.get("year");
        if (year) all = all.filter((q) => String(q.year) === year);
        const ids = params.get("ids");
        if (ids) {
          const wanted = ids.split(",");
          all = all.filter((q) => wanted.includes(String(q.id)));
        }
        return json(all);
      }
      if (pathOnly === "/api/scores/schema") {
        // 需要按年份给不同结构时用 state.schemasByYear（换年份的表单测试）
        const query = String(url).split("?")[1] || "";
        const year = Number(new URLSearchParams(query).get("year"));
        return json((state.schemasByYear || {})[year] || state.schema);
      }
      if (pathOnly === "/api/scores") {
        return method === "POST" ? json({ total_score: 112, total_full: 150 }) : json(state.records);
      }
      if (pathOnly === "/api/scores/trend") return json(state.trend);
      if (pathOnly === "/api/export") {
        return json({ filename: "408错题本_2026-09-19_1200.docx", download_url: "/api/exports/1/download",
                      question_count: 5, page_count_estimate: 3 });
      }
      if (/^\/api\/exports\/\d+\/ticket$/.test(pathOnly)) {
        const id = pathOnly.split("/")[3];
        return json({ url: `/api/exports/${id}/download?ticket=FAKE-TICKET`, expires_in: 900 });
      }
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
      source: "paper",
      paper_id: 7,
      paper_title: "2009 年真题",
      choice_groups: [
        { subject: "ds", name: "数据结构", from: 1, to: 11, count: 11, per_score: 2, full: 22 },
        { subject: "co", name: "计算机组成原理", from: 12, to: 22, count: 11, per_score: 2, full: 22 },
        { subject: "os", name: "操作系统", from: 23, to: 32, count: 10, per_score: 2, full: 20 },
        { subject: "cn", name: "计算机网络", from: 33, to: 40, count: 8, per_score: 2, full: 16 },
      ],
      subjective: [{ qno: 41, subject: "ds", name: "数据结构", full: 10 }],
      module_full: { ds: 45, co: 45, os: 35, cn: 25, total: 150 },
    },
    // 题目：2009 两道（一道选择一道主观）、2010 一道，用来测跨年份勾选与已选清单
    questions: [
      { id: 1, year: 2009, question_no: 1, subject: "ds", type: "choice", image_urls: ["/api/questions/1/images/0"] },
      { id: 2, year: 2009, question_no: 41, subject: "ds", type: "subjective", image_urls: ["/api/questions/2/images/0"] },
      { id: 3, year: 2010, question_no: 10, subject: "co", type: "choice", image_urls: ["/api/questions/3/images/0"] },
    ],
    records: [
      {
        id: 1, paper_year: 2009, practice_date: "2026-09-19", total_score: 112, total_full: 150, total_rate: 74.7,
        ds: 36, co: 32, os: 25, cn: 19, note: "第一次", rates: { ds: 80, co: 71.1, os: 71.4, cn: 76 },
        sections: {
          objective: { score: 60, full: 80, rate: 75 },
          subjective: { score: 52, full: 70, rate: 74.3 },
          total: { score: 112, full: 150, rate: 74.7 },
        },
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
        sections: { objective: 75, subjective: 74.3 },
        total: 112, total_full: 150, total_rate: 74.7, samples: [{ practice_date: "2026-09-19", paper_year: 2009, total_score: 112, weight: 1 }],
        message: "只有 1 次记录，权重已按比例归一化",
      },
    },
    exports: [
      {
        id: 1, filename: "408错题本_2026-09-19_1200.docx", created_at: "2026-09-19 12:00:00",
        question_count: 5, download_url: "/api/exports/1/download",
      },
    ],
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
    // 迷你 DOM 里没有真的 location / URL：core.js 换临时下载链接时要用它们拼绝对地址
    location: { href: "http://127.0.0.1:18100/", origin: "http://127.0.0.1:18100", reload: () => {} },
    URL,
    // 默认当普通浏览器；测安卓壳的下载流程时再改成 "... ZCWrongBook/1.0"
    navigator: { userAgent: "Mozilla/5.0 (Windows NT 10.0) Chrome/120" },
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
  /** 把一个子树里的文字都拼起来（迷你 DOM 没有 innerText，只能自己走） */
  const textOf = (node) => {
    let out = node.textContent || "";
    for (const child of node.children || []) out += " " + textOf(child);
    return out;
  };
  /** 去掉所有空白再比：拼出来的文字层级之间会多空格 */
  const squash = (text) => String(text).replace(/\s+/g, "");

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
  check("进入时**不预选**年份/科目：两个下拉都是「请选择」",
        byId.yearSelect.value === "" && byId.subjectSelect.value === "" &&
          textOf(byId.yearSelect).includes("请选择") && textOf(byId.subjectSelect).includes("请选择"),
        `年=${byId.yearSelect.value} 科=${byId.subjectSelect.value}`);
  check("没选年份就不拉题目（不一次性拉全库）",
        !fake.calls.some((c) => c.startsWith("GET /api/questions")), fake.calls.join(" | "));
  check("没选年份时题目列表是空的，并提示先选年份",
        byId.questionList.children.length === 0 && textOf(byId.pickEmpty).includes("选一个真题年份"),
        textOf(byId.pickEmpty));

  console.log("\n2a) 选年份 -> 才加载那一年的题目");
  byId.yearSelect.value = "2009";
  byId.yearSelect.fire("change");
  await tick();
  await tick();
  check("按年份要题目", fake.calls.includes("GET /api/questions?year=2009"), fake.calls.slice(-3).join(" | "));
  check("还是没拉全库", !fake.calls.includes("GET /api/questions"), fake.calls.join(" | "));
  check("渲染出 2009 的两道题", byId.questionList.children.length === 2, `${byId.questionList.children.length} 个`);
  check("年份下拉里没有「全部年份」", !textOf(byId.yearSelect).includes("全部年份"), textOf(byId.yearSelect));

  console.log("\n2b) 点底栏「已选 n 题」能看清单、逐条删、一键删除");
  byId.questionList.children[0].click();
  byId.questionList.children[1].click();
  await tick();
  check("底栏数字跟着涨", String(byId.selInfo.textContent).includes("已选 2 题"), byId.selInfo.textContent);
  check("有选中就能导出了", byId.exportBtn.disabled === false);
  byId.selInfo.click();
  await tick();
  await tick();
  check("弹出已选清单面板", !byId.selPanel.classList.contains("hidden"), byId.selPanel.className);
  check("清单按 id 现取题目（题目可能被管理员改过/删了）",
        fake.calls.some((c) => c.startsWith("GET /api/questions?ids=")), fake.calls.slice(-3).join(" | "));
  let selText = squash(textOf(byId.selList));
  check("清单是「年份第N题-科目-题型」文本",
        selText.includes("2009年第1题-数据结构-选择题") && selText.includes("2009年第41题-数据结构-主观题"), selText);
  check("面板标题写了数量", String(byId.selTitle.textContent).includes("已选 2 题"), byId.selTitle.textContent);

  const rmBtns = [];
  const walkRm = (node) => {
    for (const child of node.children || []) {
      if (String(child.className).includes("rm")) rmBtns.push(child);
      walkRm(child);
    }
  };
  walkRm(byId.selList);
  check("每条都带一个删除按钮", rmBtns.length === 2, `${rmBtns.length} 个`);
  rmBtns[0].click();
  await tick();
  check("逐条删：删掉第 1 条后只剩 1 题", String(byId.selTitle.textContent).includes("已选 1 题"),
        byId.selTitle.textContent);
  check("删掉的那题不再是选中态", !byId.questionList.children[0].classList.contains("selected"));

  // 跨年份勾选：切到 2010 再选一道，两条都要在清单里
  byId.yearSelect.value = "2010";
  byId.yearSelect.fire("change");
  await tick();
  await tick();
  check("切到 2010 只拉了 2010 那一年", fake.calls.includes("GET /api/questions?year=2010"), fake.calls.slice(-3).join(" | "));
  byId.questionList.children[0].click();
  await tick();
  byId.selInfo.click();
  await tick();
  await tick();
  selText = squash(textOf(byId.selList));
  check("跨年份的题目都在清单里，且按年份排",
        selText.includes("2009年第41题-数据结构-主观题") && selText.includes("2010年第10题-计算机组成原理-选择题"),
        selText);
  check("清单正好两条", (selText.match(/第\d+题-/g) || []).length === 2, selText);

  byId.selClear.click();
  await tick();
  check("一键删除：全清空、导出按钮禁用",
        String(byId.selTitle.textContent).includes("已选 0 题") && byId.exportBtn.disabled === true,
        `${byId.selTitle.textContent} / disabled=${byId.exportBtn.disabled}`);
  check("清空后清单给空提示", textOf(byId.selList).includes("还没选题目"), textOf(byId.selList));
  byId.selClose.click();
  check("「关闭」收起面板", byId.selPanel.classList.contains("hidden"), byId.selPanel.className);

  // 回到 2009，后面的导出用例要用
  byId.yearSelect.value = "2009";
  byId.yearSelect.fire("change");
  await tick();
  await tick();

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
  check("记录里客观/主观/合计分开显示",
        textOf(byId.recordList).includes("客观 60 / 80") && textOf(byId.recordList).includes("主观 52 / 70"),
        textOf(byId.recordList).slice(0, 120));
  check("渲染了当前水平估计", byId.estimate.children.length > 0);
  check("估计里客观/主观分开给比例",
        textOf(byId.estimate).includes("客观题 75%") && textOf(byId.estimate).includes("主观题 74.3%"),
        textOf(byId.estimate).slice(0, 120));
  check("渲染了趋势图", byId.chart.children.length > 0);
  check("顶栏显示了用户名", String(byId.whoami.textContent).includes("测试"), String(byId.whoami.textContent));

  console.log("\n4) 点「录入成绩」应展开表单");
  byId.newRecord.click();
  await tick();
  await tick();
  check("#recordForm 展开", !byId.recordForm.classList.contains("hidden"));
  check("表单里生成了控件", byId.recordForm.children.length > 3, `${byId.recordForm.children.length} 个`);

  /* 表单结构必须来自那份试卷，不能写死 1-11/12-22 + 每题 2 分 */
  let formText = textOf(byId.recordForm);
  check("表单按试卷结构显示每题分值", formText.includes("每题 2 分"), formText.slice(0, 80));
  check("表单显示了结构来源", formText.includes("2009 年真题"));
  check("表单问了 2009 年的结构", fake.calls.some((c) => c === "GET /api/scores/schema?year=2009"), fake.calls.join(" | "));

  console.log("\n4b) 换一年（DS 只有 10 题、每题 1.5 分）后表单要跟着变");
  state.schema = {
    ...state.schema,
    source: "paper",
    paper_title: "2010 年真题",
    choice_groups: [
      { subject: "ds", name: "数据结构", from: 1, to: 10, count: 10, per_score: 1.5, full: 15 },
      { subject: "co", name: "计算机组成原理", from: 11, to: 20, count: 10, per_score: 2, full: 20 },
    ],
    subjective: [{ qno: 41, subject: "ds", name: "数据结构", full: 12 }],
    module_full: { ds: 27, co: 20, os: 0, cn: 0, total: 47 },
  };
  state.catalogYears = [{ year: 2010, papers: 1, questions: 30 }];
  byClass.tab.find((t) => t.dataset.view === "pick").click();
  byClass.tab.find((t) => t.dataset.view === "scores").click();
  await tick();
  await tick();
  byId.newRecord.click();
  await tick();
  await tick();
  formText = textOf(byId.recordForm);
  check("每题分值跟着结构变", formText.includes("每题 1.5 分"), formText.slice(0, 80));
  check("题号范围跟着结构变", formText.includes("数据结构 1-10"));
  check("综合题满分跟结构（41 题 12 分）", formText.includes("第 41 题"));
  check("按新年份要了结构", fake.calls.some((c) => c === "GET /api/scores/schema?year=2010"), fake.calls.join(" | "));

  console.log("\n4c) 总分必须算上主观题（曾经只加了客观题）");
  const numeric = [];
  const selects = [];
  const walk = (node) => {
    for (const child of node.children || []) {
      if (child.tagName === "INPUT" && child.attributes.type === "number") numeric.push(child);
      if (child.tagName === "SELECT") selects.push(child);
      walk(child);
    }
  };
  walk(byId.recordForm);
  check("年份是下拉选（有真题的年份列表），不是手填数字",
        selects.some((s) => (s.children || []).some((o) => String(o.attributes.value) === "2010")),
        selects.map((s) => (s.children || []).map((o) => o.attributes.value).join("/")).join(" | "));
  check("表单里 4 个数字框（2 组选择题 / 综合题得分 / 综合题满分）", numeric.length === 4, `${numeric.length} 个`);
  const [dsCount, coCount, subjScore, subjFull] = numeric;
  dsCount.value = "10";
  dsCount.fire("input");
  coCount.value = "10";
  coCount.fire("input");
  subjFull.value = "12";
  subjFull.fire("input");
  subjScore.value = "12";
  subjScore.fire("input");
  formText = textOf(byId.recordForm);
  const totalsText = squash(formText);
  check("三张表并列：客观题 / 主观题 / 合计",
        totalsText.includes("客观题") && totalsText.includes("主观题") && totalsText.includes("合计"),
        formText.slice(-200));
  // 客观题 10×1.5 + 10×2 = 35/35，主观题 12/12，合计 47/47
  check("客观题小计 = 35 / 35（100%）", totalsText.includes("客观小计35/35100%"), totalsText.slice(-140));
  check("主观题小计 = 12 / 12（100%）", totalsText.includes("主观小计12/12100%"), totalsText.slice(-140));
  check("总分 = 客观 + 主观 = 47 / 47（100%）", totalsText.includes("总分47/47100%"), totalsText.slice(-140));

  subjScore.value = "0";
  subjScore.fire("input");
  const totalsZero = squash(textOf(byId.recordForm));
  check("主观题得 0 分时总分跟着掉到 35",
        totalsZero.includes("主观小计0/120%") && totalsZero.includes("总分35/4774%"), totalsZero.slice(-140));

  console.log("\n4d) 综合题满分必须**逐题**取结构里的值（40 分那年的真实分布）");
  /* 真实卷面 41-47 的满分是 10/15/8/13/7/8/9，各不相同。
     曾经担心过「所有主观题满分显示成同一个数」这种按科目/全局取默认值的写法，
     所以这里专门断言每一行的满分输入框各自拿到自己那一题的值。 */
  const realFulls = [10, 15, 8, 13, 7, 8, 9];
  state.schema = {
    ...state.schema,
    source: "paper",
    paper_title: "2009 年真题",
    choice_groups: [
      { subject: "ds", name: "数据结构", from: 1, to: 10, count: 10, per_score: 2, full: 20 },
      { subject: "co", name: "计算机组成原理", from: 11, to: 20, count: 10, per_score: 2, full: 20 },
    ],
    subjective: realFulls.map((full, i) => ({
      qno: 41 + i,
      subject: ["ds", "ds", "co", "co", "os", "os", "cn"][i],
      name: "x",
      full,
    })),
    module_full: { ds: 45, co: 45, os: 35, cn: 25, total: 150 },
  };
  byId.newRecord.click();
  await tick();
  await tick();
  const nums2 = [];
  const walk2 = (node) => {
    for (const child of node.children || []) {
      if (child.tagName === "INPUT" && child.attributes.type === "number") nums2.push(child);
      walk2(child);
    }
  };
  walk2(byId.recordForm);
  // 前 2 个是选择题答对数，之后每题一对（得分, 满分）
  const fullValues = realFulls.map((_, i) => Number(nums2[3 + i * 2].value));
  check("7 道综合题的满分输入框各不相同（按结构逐题取）",
        JSON.stringify(fullValues) === JSON.stringify(realFulls),
        `结构=${JSON.stringify(realFulls)} 表单=${JSON.stringify(fullValues)}`);
  const qnoHits = textOf(byId.recordForm).match(/第 \d+ 题/g) || [];
  check("7 道综合题都渲染出来了", qnoHits.length === 7, qnoHits.join("/"));
  const totalsReal = squash(textOf(byId.recordForm));
  // 客观 40 + 主观 70 = 110 满分；一道没填就是 0 分
  check("主观题小计 = 0 / 70", totalsReal.includes("主观小计0/700%"), totalsReal.slice(-140));
  check("总分满分 = 40 + 70 = 110", totalsReal.includes("总分0/1100%"), totalsReal.slice(-140));

  console.log("\n4e) 换年份后主观题满分必须换成新年份那一套（曾经一直沿用上一年的）");
  const schema2010 = { ...state.schema, year: 2010 };
  const schema2011 = {
    ...state.schema,
    year: 2011,
    paper_title: "2011 年真题",
    subjective: [10, 13, 11, 12, 8, 7, 9].map((full, i) => ({
      qno: 41 + i,
      subject: ["ds", "ds", "co", "co", "os", "os", "cn"][i],
      name: "x",
      full,
    })),
    module_full: { ds: 45, co: 45, os: 35, cn: 25, total: 150 },
  };
  state.schemasByYear = { 2010: schema2010, 2011: schema2011 };
  state.catalogYears = [{ year: 2010, papers: 1, questions: 40 }, { year: 2011, papers: 1, questions: 40 }];
  byClass.tab.find((t) => t.dataset.view === "pick").click();
  byClass.tab.find((t) => t.dataset.view === "scores").click();
  await tick();
  await tick();
  fake.calls.push("--- 换年份 ---");
  byId.newRecord.click();
  await tick();
  await tick();
  const nums3 = [];
  const walk3 = (node) => {
    for (const child of node.children || []) {
      if (child.tagName === "INPUT" && child.attributes.type === "number") nums3.push(child);
      walk3(child);
    }
  };
  walk3(byId.recordForm);
  check("表单先是 2010 年那一套满分",
        nums3[3].value === "10" && nums3[5].value === "15", `${nums3[3].value}/${nums3[5].value}`);
  nums3[2].value = "8"; // 第 41 题得分
  nums3[2].fire("input");
  nums3[4].value = "20"; // 第 42 题得分（超 2011 年的 13，应该被夹到 13）
  nums3[4].fire("input");
  const yearSelect = (() => {
    let found = null;
    const walk = (node) => {
      for (const child of node.children || []) {
        if (child.tagName === "SELECT") found = found || child;
        walk(child);
      }
    };
    walk(byId.recordForm);
    return found;
  })();
  check("年份下拉里有 2011", yearSelect.children.some((o) => String(o.attributes.value) === "2011"),
        yearSelect.children.map((o) => o.attributes.value).join("/"));
  yearSelect.value = "2011";
  yearSelect.fire("change");
  await tick();
  await tick();
  check("换年份按新年份要了结构", fake.calls.includes("GET /api/scores/schema?year=2011"), fake.calls.slice(-4).join(" | "));
  const nums4 = [];
  const walk4 = (node) => {
    for (const child of node.children || []) {
      if (child.tagName === "INPUT" && child.attributes.type === "number") nums4.push(child);
      walk4(child);
    }
  };
  walk4(byId.recordForm);
  const fulls2011 = [10, 13, 11, 12, 8, 7, 9].map((_, i) => Number(nums4[3 + i * 2].value));
  check("满分换成了 2011 年那一套",
        JSON.stringify(fulls2011) === JSON.stringify([10, 13, 11, 12, 8, 7, 9]),
        JSON.stringify(fulls2011));
  check("已经填的得分跟着带过来（41 题 8 分）", Number(nums4[2].value) === 8, nums4[2].value);
  check("得分超过新年份满分时被夹住（42 题 20 → 13）", Number(nums4[4].value) === 13, nums4[4].value);

  console.log("\n5) 生成 Word（导出记录已经去掉，生成完直接下载）");
  check("Tab 里没有「导出记录」了", !byClass.tab.some((t) => t.dataset.view === "history"),
        byClass.tab.map((t) => t.dataset.view).join("/"));
  check("页面上也没有 #view-history", !byId["view-history"]);
  byClass.tab.find((t) => t.dataset.view === "pick").click(); // 回到选错题，才点得到导出
  await tick();
  byId.questionList.children[0].click(); // 选一道题，导出按钮才会亮
  await tick();
  check("选中后导出按钮可用", byId.exportBtn.disabled === false);
  const callsBefore = fake.calls.length;
  byId.exportBtn.click();
  await tick();
  await tick();
  check("调了生成接口", fake.calls.slice(callsBefore).includes("POST /api/export"),
        fake.calls.slice(callsBefore).join(" | "));
  check("普通浏览器直接下载（不换票）",
        !fake.calls.slice(callsBefore).some((c) => c.includes("/ticket")), fake.calls.slice(callsBefore).join(" | "));
  check("普通浏览器不弹「复制链接」面板（web 端行为不变）",
        byId.dlPanel.classList.contains("hidden"), byId.dlPanel.className);

  console.log("\n5b) 安卓壳里生成完：换 15 分钟临时链接 + 弹「复制链接」面板");
  sandbox.navigator.userAgent = "Mozilla/5.0 (Linux; Android 14) ZCWrongBook/1.0";
  sandbox.location.href = "http://127.0.0.1:18100/";
  let copied = null;
  sandbox.ZCAndroid = { copy: (text) => { copied = text; } }; // 原生剪贴板桥
  byId.exportBtn.click();
  await tick();
  await tick();
  check("壳里先要了一条临时下载链接", fake.calls.slice(callsBefore).includes("POST /api/exports/1/ticket"),
        fake.calls.slice(callsBefore).join(" | "));
  check("临时链接是带票的绝对地址", byId.dlUrl.value === "http://127.0.0.1:18100/api/exports/1/download?ticket=FAKE-TICKET",
        byId.dlUrl.value);
  check("弹出了「复制链接」面板", !byId.dlPanel.classList.contains("hidden"), byId.dlPanel.className);
  check("顺手跳到该链接（壳会交给系统浏览器）",
        String(sandbox.location.href).includes("/api/exports/1/download?ticket="), String(sandbox.location.href));
  byId.dlCopy.click();
  await tick();
  await tick();
  check("点「复制链接」走原生剪贴板桥", copied === byId.dlUrl.value, String(copied));
  check("复制后提示 15 分钟有效", String(byId.dlHint.textContent).includes("15 分钟"), byId.dlHint.textContent);
  byId.dlClose.click();
  check("「关闭」能收起面板", byId.dlPanel.classList.contains("hidden"), byId.dlPanel.className);

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
