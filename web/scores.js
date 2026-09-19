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

  /* 结构来自那份试卷（各年题号范围/分值不一样）；编辑老记录时用**记录自己**的快照，
     这样后来改了试卷结构也不会把老记录重新解释一遍。 */
  async function fetchSchema(year, paperId) {
    const query = paperId ? `?paper_id=${paperId}` : year ? `?year=${year}` : "";
    return mapSchema(await api(`/api/scores/schema${query}`), year);
  }

  function mapSchema(raw, year) {
    return { ...raw, year: raw.year ?? year ?? null };
  }

  function schemaFromRecord(record) {
    const breakdown = (record.detail && record.detail.breakdown) || {};
    if (!breakdown.choice || !breakdown.subjective) return null;
    return {
      year: record.paper_year,
      source: (record.detail && record.detail.schema_source) || "record",
      paper_id: (record.detail && record.detail.paper_id) || null,
      paper_title: "",
      choice_groups: breakdown.choice.map((g) => ({
        subject: g.subject, name: g.name, from: g.from, to: g.to,
        count: g.count, per_score: g.per_score, full: g.full,
      })),
      subjective: breakdown.subjective.map((s) => ({
        qno: s.qno, subject: s.subject, name: s.name, full: s.full,
      })),
      module_full: record.module_full || { ds: 45, co: 45, os: 35, cn: 25, total: 150 },
    };
  }

  function buildForm(record = null, draft = null) {
    const box = $("#recordForm");
    box.innerHTML = "";
    box.classList.remove("hidden");
    if (!draft) state.editingId = record ? record.id : null;

    const schema = (record && schemaFromRecord(record)) || state.schema;
    const input = draft || (record && record.detail && record.detail.input) || {};
    const choices = input.choice || {};
    const subjective = input.subjective || {};
    const refs = { choice: {}, subjective: {} };

    const yearInput = numberInput(
      (draft && draft.paper_year) || (record ? record.paper_year : state.years[0]?.year) || new Date().getFullYear() - 17,
      { min: 1990, max: 2100 }
    );
    yearInput.setAttribute("list", "yearList");
    yearInput.classList.add("sm");
    const dateInput = el("input", { type: "date", class: "input sm", value: (record && record.practice_date) || input.practice_date || today() });

    const head = el("div", { class: "form-row" }, [
      el("label", {}, ["真题年份", yearInput]),
      el("label", {}, ["做题日期", dateInput]),
    ]);

    /* 选择题：填答对个数 */
    const choiceBox = el("div", { class: "form-block" }, [el("h3", {}, ["选择题（填答对个数）"])]);
    if (schema.source === "default") {
      choiceBox.appendChild(
        el("p", { class: "hint" }, [
          "该年份还没导入真题试卷，用的是默认卷面结构（1-11 / 12-22 / 23-32 / 33-40，每题 2 分）；" +
            "导入真题后会自动按那份卷子的结构算分。",
        ])
      );
    } else if (schema.paper_title) {
      choiceBox.appendChild(el("p", { class: "hint" }, [`卷面结构来自：${schema.paper_title}`]));
    }
    for (const group of schema.choice_groups) {
      const key = group.key || `g${group.from}`;
      const value = Number(choices[key] ?? choices[group.subject] ?? 0);
      const scoreCell = el("span", { class: "score-cell" }, ["—"]);
      const inputEl = numberInput(value, { min: 0, max: group.count, oninput: () => recalc() });
      refs.choice[key] = { group, key, input: inputEl, cell: scoreCell };
      choiceBox.appendChild(
        el("div", { class: "form-row tight" }, [
          el("span", { class: "label" }, [`${group.name} ${group.from}-${group.to}`]),
          el("span", { class: "hint" }, [`共 ${group.count} 题 / 每题 ${group.per_score} 分`]),
          inputEl,
          el("span", { class: "hint" }, [`/ ${group.count}`]),
          scoreCell,
        ])
      );
    }

    /* 综合题：逐题填得分与满分 */
    const subjBox = el("div", { class: "form-block" }, [el("h3", {}, ["综合题（填得分，满分默认按当年试卷）"])]);
    for (const item of schema.subjective) {
      const stored = subjective[String(item.qno)] || {};
      const fullInput = numberInput(stored.full ?? item.full, { min: 0, max: 50, step: 0.5, oninput: () => recalc() });
      const scoreInput = numberInput(stored.score ?? 0, { min: 0, max: 50, step: 0.5, oninput: () => recalc() });
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
      value: (draft && draft.note) || (record && record.note) || "",
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

    /* 实时算分：口径与后端 score_service 一致。
       客观题（选择题答对数 × 每题分值）与主观题（逐题得分）**分开算**，最后汇总总分，
       三块都给得分率 —— 以前这里只累加了选择题，导致表单上显示的总分漏掉主观题。 */
    function compute() {
      const modules = { ds: 0, co: 0, os: 0, cn: 0 };
      const moduleFull = { ds: 0, co: 0, os: 0, cn: 0 };
      const choiceDetail = [];
      let objective = 0;
      let objectiveFull = 0;
      for (const { group, input: inputEl } of Object.values(refs.choice)) {
        let correct = Math.round(Number(inputEl.value) || 0);
        correct = Math.max(0, Math.min(group.count, correct));
        const score = round1(correct * group.per_score);
        modules[group.subject] += score;
        moduleFull[group.subject] += group.full;
        objective += score;
        objectiveFull += group.full;
        choiceDetail.push({ group, correct, score });
      }
      let subjective = 0;
      let subjectiveFull = 0;
      for (const ref of Object.values(refs.subjective)) {
        const full = Math.max(0, Number(ref.full.value) || 0);
        const score = Math.max(0, Math.min(full, Number(ref.score.value) || 0));
        modules[ref.item.subject] += score;
        moduleFull[ref.item.subject] += full;
        subjective += score;
        subjectiveFull += full;
      }
      return {
        modules: { ds: round1(modules.ds), co: round1(modules.co), os: round1(modules.os), cn: round1(modules.cn) },
        moduleFull,
        choiceDetail,
        objective: round1(objective),
        objectiveFull: round1(objectiveFull),
        subjective: round1(subjective),
        subjectiveFull: round1(subjectiveFull),
      };
    }

    const rate = (score, full) => (full > 0 ? Math.round((score / full) * 100) : 0);

    function sectionRow(label, score, full, strong = false) {
      return el("div", { class: `total-item${strong ? " strong" : ""}` }, [
        el("span", { class: "k" }, [label]),
        el("b", {}, [`${score}`]),
        el("span", { class: "hint" }, [`/ ${full}（${rate(score, full)}%）`]),
      ]);
    }

    function recalc() {
      const c = compute();
      totalBox.innerHTML = "";
      for (const { group, score } of c.choiceDetail) {
        refs.choice[group.key || `g${group.from}`].cell.textContent = `${score} / ${group.full} 分`;
      }
      for (const key of ZC.MODULE_KEYS) {
        const value = c.modules[key];
        const full = c.moduleFull[key] || schema.module_full[key] || 0;
        totalBox.appendChild(
          el("div", { class: "total-item" }, [
            el("span", { class: "k", style: `color:${ZC.COLORS[key]}` }, [ZC.SUBJECT_FULL[key]]),
            el("b", {}, [`${round1(value)}`]),
            el("span", { class: "hint" }, [`/ ${full}`]),
            el("span", { class: "bar" }, [
              el("i", { style: `width:${full ? Math.min(100, (value / full) * 100) : 0}%;background:${ZC.COLORS[key]}` }),
            ]),
          ])
        );
      }
      const total = round1(c.objective + c.subjective);
      const totalFull = round1(c.objectiveFull + c.subjectiveFull);
      totalBox.appendChild(sectionRow("客观题", c.objective, c.objectiveFull));
      totalBox.appendChild(sectionRow("主观题", c.subjective, c.subjectiveFull));
      totalBox.appendChild(sectionRow("总分", total, totalFull, true));
    }

    /* 换年份要换表单：某年 DS 只有 10 个选择题、综合题分值也可能不同 */
    async function changeYear() {
      const year = Number(yearInput.value);
      if (!year || year === schema.year) return;
      try {
        const next = await fetchSchema(year, null);
        state.schema = next;
        if (sameShape(next, schema)) {
          schema.year = year;
          return;
        }
        const draft2 = {
          paper_year: year,
          practice_date: dateInput.value || today(),
          note: noteInput.value,
          choice: collectChoice(refs),
          subjective: collectSubjective(refs),
        };
        buildForm(record, draft2);
      } catch (err) {
        toast(`取不到 ${year} 年的表单结构：${err.message}`, true);
      }
    }
    yearInput.onchange = changeYear;

    function collectChoice(refsIn) {
      const out = {};
      for (const [subject, ref] of Object.entries(refsIn.choice)) {
        out[subject] = Math.max(0, Math.min(ref.group.count, Math.round(Number(ref.input.value) || 0)));
      }
      return out;
    }

    function collectSubjective(refsIn) {
      const out = {};
      for (const [qno, ref] of Object.entries(refsIn.subjective)) {
        out[qno] = { score: Math.max(0, Number(ref.score.value) || 0), full: Math.max(0, Number(ref.full.value) || 0) };
      }
      return out;
    }

    async function saveRecord(yearEl, dateEl, refsIn, noteEl) {
      const payload = {
        paper_year: Number(yearEl.value),
        practice_date: dateEl.value || today(),
        choice: collectChoice(refsIn),
        subjective: Object.entries(collectSubjective(refsIn)).map(([qno, v]) => ({
          qno: Number(qno), score: v.score, full: v.full,
        })),
        note: noteEl.value.trim(),
      };
      if (schema.paper_id) payload.paper_id = schema.paper_id;
      try {
        const path = state.editingId ? `/api/scores/${state.editingId}` : "/api/scores";
        const method = state.editingId ? "PUT" : "POST";
        const saved = await api(path, { method, body: JSON.stringify(payload) });
        const sec = saved.sections;
        toast(
          sec
            ? `已保存：客观 ${sec.objective.score}/${sec.objective.full} + 主观 ${sec.subjective.score}/${sec.subjective.full} = ${saved.total_score}/${saved.total_full}`
            : `已保存：总分 ${saved.total_score}/${saved.total_full}`
        );
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

  /* 结构有没有变（变了才需要重建表单，否则用户填一半被清空很烦） */
  function sameShape(a, b) {
    if (!a || !b) return false;
    const pack = (s) =>
      JSON.stringify([
        s.choice_groups.map((g) => [g.subject, g.from, g.to, g.per_score]),
        s.subjective.map((i) => [i.qno, i.full]),
      ]);
    return pack(a) === pack(b);
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
      row.append(head, bars);
      const sec = record.sections;
      if (sec) {
        // 客观题 / 主观题 分开看，再给汇总（比例就是得分率）
        row.appendChild(
          el("div", { class: "record-sections" }, [
            el("span", { class: "tag-sec" }, [
              `客观 ${sec.objective.score} / ${sec.objective.full}`,
              el("span", { class: "hint" }, [` ${sec.objective.rate}%`]),
            ]),
            el("span", { class: "tag-sec" }, [
              `主观 ${sec.subjective.score} / ${sec.subjective.full}`,
              el("span", { class: "hint" }, [` ${sec.subjective.rate}%`]),
            ]),
            el("span", { class: "tag-sec strong" }, [
              `合计 ${sec.total.score} / ${sec.total.full}`,
              el("span", { class: "hint" }, [` ${sec.total.rate}%`]),
            ]),
          ])
        );
      }
      row.appendChild(actions);
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

    const sec = est.sections || {};
    if (sec.objective !== null && sec.objective !== undefined) {
      // 客观题 / 主观题 分开看：总分掉了到底是哪一块拖的
      box.appendChild(
        el("div", { class: "record-sections" }, [
          el("span", { class: "tag-sec" }, [`客观题 ${sec.objective}%`]),
          el("span", { class: "tag-sec" }, [`主观题 ${sec.subjective}%`]),
          el("span", { class: "tag-sec strong" }, [`合计 ${est.total} / ${est.total_full}（${est.total_rate}%）`]),
        ])
      );
    }

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
      state.schema = await fetchSchema(state.years[0]?.year ?? null, null);
      await loadRecords();
      await loadTrend();
    } catch (err) {
      toast(`加载失败：${err.message}`, true);
    }
  }

  /* ---------------------------------------------------------- 事件 */
  $("#logoutBtn").onclick = () => ZC.session.logout();
  $("#newRecord").onclick = async () => {
    try {
      state.schema = await fetchSchema(state.years[0]?.year ?? null, null);
    } catch (err) {
      toast(`取不到表单结构：${err.message}`, true);
      return;
    }
    buildForm(null);
  };
  $("#xAxis").onchange = loadTrend;
  $("#aggregate").onchange = loadTrend;

  ZC.onShow("scores", enter);
})();
