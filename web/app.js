/* 用户端：选错题 -> 生成 Word。依赖 core.js 提供的 ZC。
   进入这一页时**不预选**任何年份/科目（下拉里都是「请选择」），选了年份才去拉那一年的题目；
   勾选跨年份累计（存 localStorage），底栏「已选 n 题」点开是清单，可逐条删 / 一键删除。 */
(() => {
  "use strict";
  const { $, api, toast, download, el } = ZC;

  const STORE_KEY = "zc.selected";

  const state = {
    years: [], // [{year, papers, questions}] —— 来自 /api/catalog
    questions: [], // 当前年份的题目（服务端已按年份过滤）
    cache: new Map(), // 年份 -> 题目列表（本地再缓存一层，切来切去不用重新请求）
    selected: new Set(JSON.parse(localStorage.getItem(STORE_KEY) || "[]").map(Number)),
    year: "", // "" = 还没选（下拉显示「请选择」）
    subject: "", // "" = 还没选（不按科目过滤）
    keyword: "",
  };

  const cards = new Map(); // 题目 id -> 卡片元素（面板里删一条时只改样式，不重渲染图片）
  const meta = new Map(); // 题目 id -> {year, question_no, subject, type} 或 null（服务端已没有）
  let selOpen = false;

  function saveSelection() {
    localStorage.setItem(STORE_KEY, JSON.stringify([...state.selected]));
  }

  /* ---------------------------------------------------------- 下拉：年份 / 科目 */
  function renderYearOptions() {
    const select = $("#yearSelect");
    select.innerHTML = "";
    select.appendChild(el("option", { value: "" }, ["请选择"]));
    for (const item of [...state.years].sort((a, b) => b.year - a.year)) {
      select.appendChild(el("option", { value: String(item.year) }, [`${item.year} 年（${item.questions} 题）`]));
    }
    select.value = state.year;
  }

  function renderSubjectOptions() {
    const select = $("#subjectSelect");
    select.innerHTML = "";
    select.appendChild(el("option", { value: "" }, ["请选择"]));
    for (const [key, name] of Object.entries(ZC.SUBJECTS)) {
      select.appendChild(el("option", { value: key }, [name]));
    }
    select.value = state.subject;
  }

  /* ---------------------------------------------------------- 题目列表 */
  function visibleQuestions() {
    const kw = state.keyword.trim();
    return state.questions.filter((q) => {
      if (state.subject && q.subject !== state.subject) return false;
      if (kw && !(`${q.year}`.includes(kw) || `${q.question_no}` === kw)) return false;
      return true;
    });
  }

  function renderQuestions() {
    const list = visibleQuestions();
    const grid = $("#questionList");
    grid.innerHTML = "";
    cards.clear();
    for (const q of state.questions) rememberMeta(q);

    const empty = $("#pickEmpty");
    empty.classList.toggle("hidden", list.length > 0);
    if (!state.year) {
      empty.textContent = state.years.length
        ? "先在上面选一个真题年份（一次只加载这一年，题目多也不卡）。"
        : "还没有真题数据，请先在管理后台导入真题 PDF。";
    } else if (!list.length) {
      empty.textContent = `${state.year} 年这一组筛选下没有题目（换个年份，或把科目切回「请选择」看全部）。`;
    }

    const frag = document.createDocumentFragment();
    for (const q of list) {
      const card = el("div", { class: `card${state.selected.has(q.id) ? " selected" : ""}` });
      const img = el("img", { loading: "lazy", src: q.image_urls[0] || "", alt: `${q.year} 第 ${q.question_no} 题` });
      card.appendChild(img);
      card.insertAdjacentHTML(
        "beforeend",
        `<span class="year">${q.year}</span><span class="tick">✓</span>
         <div class="meta"><span class="no">${q.question_no}.</span>
         <span class="badge ${q.subject}">${ZC.SUBJECTS[q.subject] || q.subject}</span>
         <span class="sub">${q.type === "choice" ? "选择" : "主观"}</span></div>`
      );
      card.onclick = () => toggleQuestion(q.id);
      cards.set(q.id, card);
      frag.appendChild(card);
    }
    grid.appendChild(frag);
    updateBar();
  }

  function rememberMeta(q) {
    meta.set(Number(q.id), {
      year: q.year,
      question_no: q.question_no,
      subject: q.subject,
      type: q.type,
    });
  }

  /* ---------------------------------------------------------- 勾选 */
  function syncCards() {
    for (const [id, card] of cards) card.classList.toggle("selected", state.selected.has(id));
  }

  function toggleQuestion(id) {
    const key = Number(id);
    if (state.selected.has(key)) state.selected.delete(key);
    else state.selected.add(key);
    saveSelection();
    updateBar();
  }

  function removeQuestion(id) {
    if (!state.selected.delete(Number(id))) return;
    saveSelection();
    updateBar();
  }

  function clearSelection() {
    if (!state.selected.size) return;
    state.selected.clear();
    saveSelection();
    updateBar();
  }

  function updateBar() {
    const count = state.selected.size;
    const info = $("#selInfo");
    info.textContent = count ? `已选 ${count} 题（点开可删）` : "已选 0 题";
    info.classList.toggle("empty", count === 0);
    info.disabled = count === 0;
    $("#exportBtn").disabled = count === 0;
    $("#selTitle").textContent = `已选 ${count} 题`;
    $("#selClear").disabled = count === 0;
    syncCards();
    if (selOpen) renderSelList();
  }

  /* ---------------------------------------------------------- 已选清单面板 */
  function labelOf(id) {
    const m = meta.get(Number(id));
    if (!m) return `题目 #${id}（服务端已经没有这道题了，删掉吧）`;
    const kind = m.type === "choice" ? "选择题" : "主观题";
    // 清单里用**全称**（"计算机组成原理"），卡片上那个短名（"计组"）太简略了
    return `${m.year}年第${m.question_no}题-${ZC.SUBJECT_FULL[m.subject] || m.subject}-${kind}`;
  }

  /** 每次打开清单都按 id 现取一次：题目可能被管理员改过题号/科目，也可能已经被删了 */
  async function refreshSelMeta() {
    const ids = [...state.selected].map(Number);
    if (!ids.length) return;
    try {
      const list = await api(`/api/questions?ids=${ids.join(",")}`);
      const got = new Set();
      for (const q of list) {
        rememberMeta(q);
        got.add(Number(q.id));
      }
      // 服务端没返回的说明题目已经被删除：标成 null，面板里提示用户删掉
      for (const id of ids) if (!got.has(id)) meta.set(id, null);
    } catch (err) {
      toast(`取已选题目失败：${err.message}`, true);
    }
  }

  function renderSelList() {
    const box = $("#selList");
    box.innerHTML = "";
    const items = [...state.selected]
      .map(Number)
      .map((id) => ({ id, m: meta.get(id) }))
      .sort((a, b) => {
        const ay = a.m ? a.m.year : 0;
        const by = b.m ? b.m.year : 0;
        if (ay !== by) return ay - by;
        const an = a.m ? a.m.question_no : a.id;
        const bn = b.m ? b.m.question_no : b.id;
        return an - bn;
      });
    if (!items.length) {
      box.appendChild(el("p", { class: "empty" }, ["还没选题目：回上面点题目卡片就行。"]));
      return;
    }
    for (const { id, m } of items) {
      const rm = el("button", { class: "rm", title: "删掉这一题" }, ["✕"]);
      rm.onclick = () => removeQuestion(id);
      box.appendChild(
        el("div", { class: "sel-item" }, [el("span", { class: "t" }, [labelOf(id)]), rm])
      );
    }
  }

  async function openSelPanel() {
    if (!state.selected.size) return;
    selOpen = true;
    $("#selPanel").classList.remove("hidden");
    renderSelList(); // 先用本地记下的元数据渲染，不等网络
    await refreshSelMeta();
    renderSelList();
  }

  function closeSelPanel() {
    selOpen = false;
    $("#selPanel").classList.add("hidden");
  }

  /* ---------------------------------------------------------- 数据 */
  async function loadCatalog() {
    const data = await api("/api/catalog");
    state.years = data.years || [];
    renderYearOptions();
  }

  async function loadQuestions() {
    if (!state.year) {
      state.questions = [];
      renderQuestions();
      return;
    }
    $("#pickLoading").classList.remove("hidden");
    try {
      if (!state.cache.has(state.year)) {
        // 服务端按年份过滤（每年最多几十道题），本地再缓存一层，切回来就不用再请求
        state.cache.set(state.year, await api(`/api/questions?year=${state.year}`));
      }
      state.questions = state.cache.get(state.year);
      renderQuestions();
    } catch (err) {
      toast(`加载题目失败：${err.message}`, true);
    } finally {
      $("#pickLoading").classList.add("hidden");
    }
  }

  async function exportBook() {
    const btn = $("#exportBtn");
    btn.disabled = true;
    btn.textContent = "生成中…";
    try {
      const result = await api("/api/export", {
        method: "POST",
        body: JSON.stringify({
          question_ids: [...state.selected],
          with_caption: $("#optCaption").checked,
          note_lines: Number($("#optNote").value) || 0,
          image_width_cm: Number($("#optWidth").value) || 16,
        }),
      });
      toast(`已生成：${result.filename}（约 ${result.page_count_estimate} 页）`);
      download(result.download_url);
    } catch (err) {
      toast(`导出失败：${err.message}`, true);
    } finally {
      btn.textContent = "生成 Word 错题本";
      updateBar();
    }
  }

  /* ---------------------------------------------------------- 事件 */
  $("#yearSelect").onchange = (e) => {
    state.year = e.target.value || "";
    loadQuestions();
  };
  $("#subjectSelect").onchange = (e) => {
    state.subject = e.target.value || "";
    renderQuestions();
  };
  $("#selectAll").onclick = () => {
    if (!state.year) {
      toast("先选一个真题年份");
      return;
    }
    visibleQuestions().forEach((q) => state.selected.add(q.id));
    saveSelection();
    updateBar();
  };
  $("#clearAll").onclick = () => {
    clearSelection();
    toast("已清空已选题目");
  };
  $("#keyword").oninput = (e) => {
    state.keyword = e.target.value;
    renderQuestions();
  };
  $("#selInfo").onclick = openSelPanel;
  $("#selClose").onclick = closeSelPanel;
  $("#selClear").onclick = () => {
    const count = state.selected.size;
    clearSelection();
    toast(`已删除全部 ${count} 题`);
  };
  $("#exportBtn").onclick = exportBook;

  // 登录成功后才会被调用（见 core.js 的登录门）
  ZC.onEnter(async () => {
    renderSubjectOptions();
    renderYearOptions();
    await loadCatalog();
    // 进来**不预选**年份/科目，等用户自己选；先把空状态和底栏摆好
    renderQuestions();
    updateBar();
  });
})();
