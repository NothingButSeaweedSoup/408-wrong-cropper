/* 用户端：选错题 -> 生成 Word；导出记录。依赖 core.js 提供的 ZC。 */
(() => {
  "use strict";
  const { $, api, toast, download, el } = ZC;

  const STORE_KEY = "zc.selected";
  const YEAR_KEY = "zc.lastYear";

  const state = {
    years: [], // [{year, papers, questions}] —— 来自公开接口 /api/catalog
    questions: [], // 当前年份的题目（服务端已按年份过滤）
    cache: new Map(), // 年份 -> 题目列表（本地再缓存一层，切来切去不用重新请求）
    selected: new Set(JSON.parse(localStorage.getItem(STORE_KEY) || "[]")),
    year: "",
    subject: "all",
    keyword: "",
  };

  function saveSelection() {
    localStorage.setItem(STORE_KEY, JSON.stringify([...state.selected]));
  }

  /* ---------------------------------------------------------- 渲染 */
  function renderYearChips() {
    const box = $("#yearChips");
    box.innerHTML = "";
    const years = [...state.years].sort((a, b) => b.year - a.year);
    if (!years.length) {
      box.appendChild(el("span", { class: "hint" }, ["还没有导入真题，去管理后台上传 PDF"]));
      return;
    }
    // 一次只加载一个年份：题目多的时候全量拉下来对服务器和手机都不友好
    for (const item of years) {
      const value = String(item.year);
      const btn = el("button", { class: `chip${state.year === value ? " active" : ""}` }, [
        `${item.year} 年 · ${item.questions} 题`,
      ]);
      btn.onclick = () => {
        state.year = value;
        localStorage.setItem(YEAR_KEY, value);
        renderYearChips();
        loadQuestions();
      };
      box.appendChild(btn);
    }
  }

  function renderSubjectChips() {
    const box = $("#subjectChips");
    box.innerHTML = "";
    const items = [["all", "全部科目"], ...Object.entries(ZC.SUBJECTS)];
    for (const [value, label] of items) {
      const btn = el("button", { class: `chip${state.subject === value ? " active" : ""}` }, [label]);
      btn.onclick = () => {
        state.subject = value;
        renderSubjectChips();
        renderQuestions();
      };
      box.appendChild(btn);
    }
  }

  function visibleQuestions() {
    const kw = state.keyword.trim();
    return state.questions.filter((q) => {
      if (state.subject !== "all" && q.subject !== state.subject) return false;
      if (kw && !(`${q.year}`.includes(kw) || `${q.question_no}` === kw)) return false;
      return true;
    });
  }

  function renderQuestions() {
    const list = visibleQuestions();
    const grid = $("#questionList");
    grid.innerHTML = "";
    const empty = $("#pickEmpty");
    empty.classList.toggle("hidden", list.length > 0);
    if (!list.length && state.year) {
      empty.textContent = `${state.year} 年这一组筛选下没有题目（换个年份或把科目切回「全部科目」）。`;
    } else if (!list.length) {
      empty.textContent = "还没有真题数据，请先在管理后台导入真题 PDF。";
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
      card.onclick = () => {
        if (state.selected.has(q.id)) state.selected.delete(q.id);
        else state.selected.add(q.id);
        saveSelection();
        card.classList.toggle("selected");
        updateBar();
      };
      frag.appendChild(card);
    }
    grid.appendChild(frag);
    updateBar();
  }

  function updateBar() {
    const count = state.selected.size;
    $("#selInfo").textContent = `已选 ${count} 题`;
    $("#exportBtn").disabled = count === 0;
  }

  async function renderHistory() {
    const items = await api("/api/exports");
    const box = $("#historyList");
    box.innerHTML = "";
    $("#historyEmpty").classList.toggle("hidden", items.length > 0);
    for (const item of items) {
      const row = el("div", { class: "item" });
      row.innerHTML = `<div class="name">${ZC.esc(item.filename)}<div class="time">${item.created_at} · ${item.question_count} 题</div></div>`;
      const dl = el("button", { class: "btn primary" }, ["下载"]);
      dl.onclick = () => download(item.download_url);
      const del = el("button", { class: "btn ghost" }, ["删除"]);
      del.onclick = async () => {
        if (!confirm("删除这条导出记录及文件？")) return;
        await api(`/api/exports/${item.id}`, { method: "DELETE" });
        renderHistory();
      };
      row.append(dl, del);
      box.appendChild(row);
    }
  }

  /* ---------------------------------------------------------- 数据 */
  async function loadCatalog() {
    const data = await api("/api/catalog");
    state.years = data.years || [];
    // 默认选最新的一年（而不是"全部年份"）；记住上次看的那一年
    const latest = [...state.years].sort((a, b) => b.year - a.year)[0];
    const remembered = localStorage.getItem(YEAR_KEY);
    const known = state.years.some((y) => String(y.year) === remembered);
    state.year = known ? remembered : latest ? String(latest.year) : "";
    renderYearChips();
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
      renderHistory();
    } catch (err) {
      toast(`导出失败：${err.message}`, true);
    } finally {
      btn.textContent = "生成 Word 错题本";
      updateBar();
    }
  }

  /* ---------------------------------------------------------- 事件 */
  $("#selectAll").onclick = () => {
    visibleQuestions().forEach((q) => state.selected.add(q.id));
    saveSelection();
    renderQuestions();
  };
  $("#clearAll").onclick = () => {
    state.selected.clear();
    saveSelection();
    renderQuestions();
  };
  $("#keyword").oninput = (e) => {
    state.keyword = e.target.value;
    renderQuestions();
  };
  $("#exportBtn").onclick = exportBook;

  ZC.onShow("history", renderHistory);

  // 登录成功后才会被调用（见 core.js 的登录门）
  ZC.onEnter(async () => {
    renderSubjectChips();
    await loadCatalog();
    await loadQuestions();
    updateBar();
  });
})();
