/* 得分记录 + 趋势。登录态由 core.js 的登录门统一管，这里只管数据。 */
(() => {
  "use strict";
  const { $, api, toast, el, round1 } = ZC;

  const state = {
    schema: null,
    records: [],
    years: [],
    chartHidden: new Set(),
    editingId: null,
  };

  const today = () => new Date().toISOString().slice(0, 10);

  /* ---------------------------------------------------------- 录入表单 */
  function numberInput(value, { min = 0, max = 999, step = 1, oninput } = {}) {
    const input = el("input", { type: "number", class: "input num", value, min, max, step });
    if (oninput) input.addEventListener("input", oninput);
    return input;
  }

  function yearDatalist() {
    const list = el("datalist", { id: "yearList" });
    for (const item of state.years) list.appendChild(el("option", { value: item.year }));
    return list;
  }

  function buildForm(record = null) {
    const box = $("#recordForm");
    box.innerHTML = "";
    box.classList.remove("hidden");
    state.editingId = record ? record.id : null;

    const input = (record && record.detail && record.detail.input) || {};
    const choices = input.choice || {};
    const subjective = input.subjective || {};
    const refs = { choice: {}, subjective: {} };

    const yearInput = numberInput(record ? record.paper_year : state.years[0]?.year || new Date().getFullYear() - 17, {
      min: 1990,
      max: 2100,
    });
    yearInput.setAttribute("list", "yearList");
    yearInput.classList.add("sm");
    const dateInput = el("input", { type: "date", class: "input sm", value: (record && record.practice_date) || today() });

    const head = el("div", { class: "form-row" }, [
      el("label", {}, ["真题年份", yearInput]),
      el("label", {}, ["做题日期", dateInput]),
    ]);

    /* 选择题：填答对个数 */
    const choiceBox = el("div", { class: "form-block" }, [
      el("h3", {}, [`选择题（每题 ${state.schema.choice_groups[0].per_score} 分，填答对个数）`]),
    ]);
    for (const group of state.schema.choice_groups) {
      const value = Number(choices[group.subject] ?? 0);
      const scoreCell = el("span", { class: "score-cell" }, ["—"]);
      const inputEl = numberInput(value, { min: 0, max: group.count, oninput: () => recalc() });
      refs.choice[group.subject] = { group, input: inputEl, cell: scoreCell };
      choiceBox.appendChild(
        el("div", { class: "form-row tight" }, [
          el("span", { class: "label" }, [`${group.name} ${group.from}-${group.to}`]),
          el("span", { class: "hint" }, [`共 ${group.count} 题 / ${group.full} 分`]),
          inputEl,
          el("span", { class: "hint" }, [`/ ${group.count}`]),
          scoreCell,
        ])
      );
    }

    /* 综合题：逐题填得分与满分 */
    const subjBox = el("div", { class: "form-block" }, [el("h3", {}, ["综合题（填得分，满分可按当年试卷改）"])]);
    for (const item of state.schema.subjective) {
      const stored = subjective[String(item.qno)] || {};
      const fullInput = numberInput(stored.full ?? item.full, { min: 0, max: 30, step: 0.5, oninput: () => recalc() });
      const scoreInput = numberInput(stored.score ?? 0, { min: 0, max: 30, step: 0.5, oninput: () => recalc() });
      refs.subjective[item.qno] = { item, score: scoreInput, full: fullInput };
      subjBox.appendChild(
        el("div", { class: "form-row tight" }, [
          el("span", { class: "label" }, [`第 ${item.qno} 题`]),
          el("span", { class: "hint" }, [ZC.SUBJECT_FULL[item.subject]]),
          scoreInput,
          el("span", { class: "hint" }, ["得分 /"]),
          fullInput,
          el("span", { class: "hint" }, ["满分"]),
        ])
      );
    }

    const noteInput = el("input", {
      type: "text",
      class: "input",
      placeholder: "备注（可选，比如「第一次做」）",
      value: (record && record.note) || "",
    });

    const totalBox = el("div", { class: "totals" });
    const saveBtn = el("button", { class: "btn primary" }, [record ? "保存修改" : "保存这次成绩"]);
    saveBtn.onclick = () => saveRecord(yearInput, dateInput, refs, noteInput);
    const cancelBtn = el("button", { class: "btn ghost" }, ["取消"]);
    cancelBtn.onclick = () => {
      box.classList.add("hidden");
      state.editingId = null;
    };

    box.append(
      el("h2", {}, [record ? `编辑：${record.paper_year} 年（${record.practice_date}）` : "录入一次成绩"]),
      yearDatalist(),
      head,
      choiceBox,
      subjBox,
      el("div", { class: "form-row" }, [el("label", { class: "grow" }, ["备注", noteInput])]),
      totalBox,
      el("div", { class: "form-actions" }, [saveBtn, cancelBtn])
    );

    /* 实时算分：口径与后端 score_service 一致 */
    function compute() {
      const modules = { ds: 0, co: 0, os: 0, cn: 0 };
      const choiceDetail = [];
      for (const { group, input: inputEl } of Object.values(refs.choice)) {
        let correct = Math.round(Number(inputEl.value) || 0);
        correct = Math.max(0, Math.min(group.count, correct));
        const score = round1(correct * group.per_score);
        modules[group.subject] += score;
        choiceDetail.push({ group, correct, score });
      }
      return { modules, choiceDetail };
    }

    function recalc() {
      const { modules, choiceDetail } = compute();
      totalBox.innerHTML = "";
      for (const { group, score } of choiceDetail) {
        refs.choice[group.subject].cell.textContent = `${score} / ${group.full} 分`;
      }
      let total = 0;
      for (const [key, value] of Object.entries(modules)) {
        total += value;
        const full = state.schema.module_full[key];
        totalBox.appendChild(
          el("div", { class: "total-item" }, [
            el("span", { class: "k", style: `color:${ZC.COLORS[key]}` }, [ZC.SUBJECT_FULL[key]]),
            el("b", {}, [`${round1(value)}`]),
            el("span", { class: "hint" }, [`/ ${full}`]),
            el("span", { class: "bar" }, [
              el("i", { style: `width:${Math.min(100, (value / full) * 100)}%;background:${ZC.COLORS[key]}` }),
            ]),
          ])
        );
      }
      const full = state.schema.module_full.total;
      totalBox.appendChild(
        el("div", { class: "total-item strong" }, [
          el("span", { class: "k" }, ["总分"]),
          el("b", {}, [`${round1(total)}`]),
          el("span", { class: "hint" }, [`/ ${full}（${Math.round((total / full) * 100)}%）`]),
        ])
      );
    }

    async function saveRecord(yearEl, dateEl, refsIn, noteEl) {
      const payload = {
        paper_year: Number(yearEl.value),
        practice_date: dateEl.value || today(),
        choice: {},
        subjective: [],
        note: noteEl.value.trim(),
      };
      for (const [subject, ref] of Object.entries(refsIn.choice)) {
        payload.choice[subject] = Math.max(0, Math.min(ref.group.count, Math.round(Number(ref.input.value) || 0)));
      }
      for (const [qno, ref] of Object.entries(refsIn.subjective)) {
        payload.subjective.push({
          qno: Number(qno),
          score: Math.max(0, Number(ref.score.value) || 0),
          full: Math.max(0, Number(ref.full.value) || 0),
        });
      }
      try {
        const path = state.editingId ? `/api/scores/${state.editingId}` : "/api/scores";
        const method = state.editingId ? "PUT" : "POST";
        const saved = await api(path, { method, body: JSON.stringify(payload) });
        toast(`已保存：总分 ${saved.total_score}/${saved.total_full}`);
        box.classList.add("hidden");
        state.editingId = null;
        await loadRecords();
        await loadTrend();
      } catch (err) {
        toast(`保存失败：${err.message}`, true);
      }
    }

    recalc();
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ---------------------------------------------------------- 记录列表 */
  function renderRecords() {
    const box = $("#recordList");
    box.innerHTML = "";
    if (!state.records.length) {
      box.appendChild(el("p", { class: "empty" }, ["还没有成绩记录，点右上角「录入成绩」开始。"]));
      return;
    }
    for (const record of state.records) {
      const row = el("div", { class: "record" });
      const bars = el("div", { class: "record-bars" });
      for (const key of ZC.MODULE_KEYS) {
        bars.appendChild(
          el("span", { class: "mini" }, [
            el("i", { style: `background:${ZC.COLORS[key]}` }),
            `${ZC.SUBJECT_FULL[key]} ${round1(record[key])}`,
          ])
        );
      }
      const head = el("div", { class: "record-head" }, [
        el("b", {}, [`${record.paper_year} 年`]),
        el("span", { class: "hint" }, [record.practice_date]),
        el("span", { class: "spacer" }),
        el("span", { class: "total" }, [`${record.total_score} / ${record.total_full}`]),
        el("span", { class: "rate" }, [`${record.total_rate}%`]),
      ]);
      const actions = el("div", { class: "record-actions" });
      const editBtn = el("button", { class: "btn tiny" }, ["编辑"]);
      editBtn.onclick = () => buildForm(record);
      const delBtn = el("button", { class: "btn tiny danger" }, ["删除"]);
      delBtn.onclick = async () => {
        if (!confirm(`删除 ${record.practice_date} 这次 ${record.paper_year} 年的成绩？`)) return;
        try {
          await api(`/api/scores/${record.id}`, { method: "DELETE" });
          await loadRecords();
          await loadTrend();
          toast("已删除");
        } catch (err) {
          toast(`删除失败：${err.message}`, true);
        }
      };
      actions.append(editBtn, delBtn);
      row.append(head, bars, actions);
      if (record.note) row.appendChild(el("div", { class: "hint note" }, [`备注：${record.note}`]));
      box.appendChild(row);
    }
  }

  async function loadRecords() {
    state.records = await api("/api/scores");
    renderRecords();
  }

  /* ---------------------------------------------------------- 当前水平估计（曲线图上方的小表格） */
  function renderEstimate(est) {
    const box = $("#estimate");
    box.innerHTML = "";
    if (!est || !est.count) {
      box.appendChild(el("p", { class: "hint" }, ["当前水平估计：还没有成绩记录"]));
      return;
    }
    const wText = est.weights.map((w) => `${Math.round(w * 100)}%`).join(" / ");
    box.appendChild(
      el("div", { class: "estimate-head" }, [
        el("strong", {}, ["当前水平估计"]),
        el("span", { class: "hint" }, [
          `近 ${est.count} 次做题加权 ${wText}` + (est.count < 3 ? "（权重已归一化）" : ""),
        ]),
      ])
    );

    const columns = [...ZC.MODULE_KEYS.map((key) => [key, ZC.SUBJECT_FULL[key]]), ["total", "总分"]];
    const table = el("table", { class: "estimate-table" });
    const headRow = el("tr");
    for (const [key, name] of columns) {
      const th = el("th", { class: key === "total" ? "hl" : "" });
      th.appendChild(el("i", { style: `background:${ZC.COLORS[key]}` }));
      th.appendChild(document.createTextNode(name));
      headRow.appendChild(th);
    }
    const valueRow = el("tr");
    for (const [key] of columns) {
      if (key === "total") {
        const td = el("td", { class: "hl" }, [`${est.total} / ${est.total_full}`]);
        td.appendChild(el("span", { class: "hint" }, [` ${est.total_rate}%`]));
        valueRow.appendChild(td);
      } else {
        valueRow.appendChild(el("td", {}, [`${est.modules[key]}%`]));
      }
    }
    table.append(el("thead", {}, [headRow]), el("tbody", {}, [valueRow]));
    box.appendChild(table);

    box.appendChild(
      el("p", { class: "hint" }, [
        "样本：" +
          est.samples
            .map((s) => `${s.practice_date} ${s.paper_year}年 ${s.total_score}分（${Math.round(s.weight * 100)}%）`)
            .join(" · "),
      ])
    );
  }

  /* ---------------------------------------------------------- 趋势 */
  async function loadTrend() {
    const xAxis = $("#xAxis").value;
    const aggregate = $("#aggregate").value;
    $("#aggregate").classList.toggle("hidden", xAxis !== "paper_year");
    try {
      const data = await api(`/api/scores/trend?x_axis=${xAxis}&aggregate=${aggregate}`);
      renderEstimate(data.estimate);
      ZC.chart.render($("#chart"), data, { hidden: state.chartHidden });
    } catch (err) {
      $("#chart").innerHTML = "";
      $("#chart").appendChild(el("p", { class: "empty" }, [`趋势加载失败：${err.message}`]));
    }
  }

  /* ---------------------------------------------------------- 进入得分页 */
  async function enter() {
    const user = ZC.session.user();
    $("#whoami").textContent = user ? `${user.display_name}（${user.username}）` : "";
    try {
      const catalog = await api("/api/catalog");
      state.years = catalog.years || [];
      state.schema = state.schema || (await api("/api/scores/schema"));
      await loadRecords();
      await loadTrend();
    } catch (err) {
      toast(`加载失败：${err.message}`, true);
    }
  }

  /* ---------------------------------------------------------- 事件 */
  $("#logoutBtn").onclick = () => ZC.session.logout();
  $("#newRecord").onclick = async () => {
    if (!state.schema) state.schema = await api("/api/scores/schema");
    buildForm(null);
  };
  $("#xAxis").onchange = loadTrend;
  $("#aggregate").onchange = loadTrend;

  ZC.onShow("scores", enter);
})();
