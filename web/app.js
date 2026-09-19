/* 用户端：选错题 -> 生成 Word；导出记录。依赖 core.js 提供的 ZC。 */
(() => {
  "use strict";
  const { $, api, toast, download, el } = ZC;

  const STORE_KEY = "zc.selected";

  const state = {
    years: [], // [{year, papers, questions}] —— 来自公开接口 /api/catalog
    questions: [],
    selected: new Set(JSON.parse(localStorage.getItem(STORE_KEY) || "[]")),
    year: "all",
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
    const items = [["all", "全部年份"], ...years.map((y) => [String(y.year), `${y.year} 年 · ${y.questions} 题`])];
    for (const [value, label] of items) {
      const btn = el("button", { class: `chip${state.year === value ? " active" : ""}` }, [label]);
      btn.onclick = () => {
        state.year = value;
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
      if (state.year !== "all" && String(q.year) !== state.year) return false;
      if (kw && !(`${q.year}`.includes(kw) || `${q.question_no}` === kw)) return false;
      return true;
    });
  }

  function renderQuestions() {
    const list = visibleQuestions();
    const grid = $("#questionList");
    grid.innerHTML = "";
    $("#pickEmpty").classList.toggle("hidden", list.length > 0);

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
    renderYearChips();
  }

  async function loadQuestions() {
    const query = state.year === "all" ? "" : `?year=${state.year}`;
    state.questions = await api(`/api/questions${query}`);
    renderQuestions();
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
