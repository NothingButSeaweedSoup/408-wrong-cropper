/* 自绘 SVG 折线图：模块得分率(左轴 0~100%) + 总分(右轴 0~150)。
   不引 ECharts：离线可用、体积几 KB、样式和页面统一。 */
window.ZC = window.ZC || {};

ZC.chart = (() => {
  "use strict";

  const W = 760;
  const H = 380;
  const PAD = { left: 48, right: 50, top: 18, bottom: 54 };
  const MODULES = ["ds", "co", "os", "cn"];
  const NS = "http://www.w3.org/2000/svg";

  const node = (tag, attrs = {}) => {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };

  const SUBJECT_FULL = { ds: "数据结构", co: "计组", os: "操作系统", cn: "计网" };

  /**
   * 画趋势图。
   * @param {HTMLElement} host 容器
   * @param {{labels:string[], series:Object, points:Object[], module_full:Object, x_axis:string}} data
   * @param {{hidden?:Set<string>}} opts 通过图例隐藏的系列
   */
  function render(host, data, opts = {}) {
    host.innerHTML = "";
    const hidden = opts.hidden || new Set();
    const labels = data.labels || [];

    if (!labels.length) {
      host.appendChild(ZC.el("p", { class: "empty" }, ["暂无记录，先录入一次成绩吧"]));
      return;
    }

    const plotW = W - PAD.left - PAD.right;
    const plotH = H - PAD.top - PAD.bottom;
    const n = labels.length;
    const xAt = (i) => (n === 1 ? PAD.left + plotW / 2 : PAD.left + (plotW * i) / (n - 1));
    const yPct = (v) => PAD.top + plotH * (1 - Math.max(0, Math.min(100, v)) / 100);
    const yTotal = (v) => PAD.top + plotH * (1 - Math.max(0, Math.min(150, v)) / 150);

    const totalFull = (data.module_full && data.module_full.total) || 150;
    const svg = node("svg", {
      viewBox: `0 0 ${W} ${H}`,
      class: "chart-svg",
      preserveAspectRatio: "xMidYMid meet",
      role: "img",
    });

    /* 网格 + 双 Y 轴刻度 */
    for (let i = 0; i <= 4; i += 1) {
      const y = PAD.top + (plotH * i) / 4;
      svg.appendChild(node("line", { x1: PAD.left, y1: y, x2: W - PAD.right, y2: y, class: "grid" }));
      const left = node("text", { x: PAD.left - 8, y: y + 4, class: "axis-label", "text-anchor": "end" });
      left.textContent = `${100 - i * 25}%`;
      svg.appendChild(left);
      const right = node("text", { x: W - PAD.right + 8, y: y + 4, class: "axis-label axis-right" });
      right.textContent = `${Math.round((totalFull * (4 - i)) / 4)}`;
      svg.appendChild(right);
    }

    /* X 轴标签（点多时抽稀） */
    const stride = Math.ceil(n / 8);
    labels.forEach((label, i) => {
      if (i % stride !== 0 && i !== n - 1) return;
      const text = node("text", { x: xAt(i), y: H - PAD.bottom + 20, class: "axis-label", "text-anchor": "middle" });
      text.textContent = label.length > 10 ? label.slice(5) : label;
      svg.appendChild(text);
    });

    /* 折线 + 数据点 */
    const plotted = {};
    const draw = (key, values, toY, color, { dashed = false, shape = "circle" } = {}) => {
      if (hidden.has(key) || !values.length) return;
      const d = values.map((v, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${toY(v).toFixed(1)}`).join(" ");
      svg.appendChild(
        node("path", {
          d,
          fill: "none",
          stroke: color,
          "stroke-width": key === "total" ? 2.6 : 2,
          "stroke-linejoin": "round",
          "stroke-linecap": "round",
          "stroke-dasharray": dashed ? "6 4" : "",
          class: "series",
        })
      );
      plotted[key] = values.map((v, i) => ({ x: xAt(i), y: toY(v), value: v }));
      values.forEach((v, i) => {
        const common = { cx: xAt(i), cy: toY(v), fill: "#fff", stroke: color, "stroke-width": 2, r: 4, class: "dot" };
        if (shape === "circle") {
          svg.appendChild(node("circle", common));
        } else {
          const s = 4;
          svg.appendChild(
            node("rect", { x: xAt(i) - s, y: toY(v) - s, width: s * 2, height: s * 2, fill: color, rx: 1.5, class: "dot" })
          );
        }
      });
    };

    draw("total", data.series.total || [], yTotal, ZC.COLORS.total, { dashed: true, shape: "rect" });
    MODULES.forEach((key) => {
      const color = ZC.COLORS[key];
      draw(key, (data.series && data.series[key]) || [], yPct, color);
    });

    /* 悬浮查看：整列热区 + HTML tooltip */
    const hover = node("g", { class: "hover-layer" });
    const tip = ZC.el("div", { class: "chart-tip hidden" });
    labels.forEach((_, i) => {
      const width = n === 1 ? plotW : plotW / Math.max(1, n - 1);
      const rect = node("rect", {
        x: xAt(i) - width / 2,
        y: PAD.top,
        width,
        height: plotH,
        fill: "transparent",
        class: "hover-band",
      });
      rect.addEventListener("pointerenter", () => showTip(i));
      rect.addEventListener("pointermove", () => showTip(i));
      hover.appendChild(rect);
    });
    svg.appendChild(hover);

    const markers = [];
    function showTip(index) {
      markers.forEach((m) => m.remove());
      markers.length = 0;
      const x = xAt(index);
      markers.push(node("line", { x1: x, y1: PAD.top, x2: x, y2: PAD.top + plotH, class: "cursor" }));
      svg.appendChild(markers[0]);
      const point = (data.points || [])[index] || {};
      const rows = MODULES.filter((k) => !hidden.has(k)).map(
        (k) =>
          `<div class="tip-row"><i style="background:${ZC.COLORS[k]}"></i>${SUBJECT_FULL[k]}
           <b>${((data.series[k] || [])[index] ?? 0).toFixed(1)}%</b></div>`
      );
      if (!hidden.has("total")) {
        rows.push(
          `<div class="tip-row"><i style="background:${ZC.COLORS.total}"></i>总分 <b>${point.total_score ?? "-"}</b> / ${totalFull}</div>`
        );
      }
      const head = data.x_axis === "paper_year"
        ? `${labels[index]} 年${point.count > 1 ? `（${point.count} 次记录）` : ""}`
        : `${labels[index]} 做过 ${point.year ?? ""} 年真题`;
      tip.innerHTML = `<div class="tip-head">${ZC.esc(head)}</div>${rows.join("")}`;
      tip.classList.remove("hidden");
      const ratio = x / W;
      tip.style.left = `${Math.min(78, Math.max(2, ratio * 100))}%`;
      tip.style.top = "4px";
    }

    const wrap = ZC.el("div", { class: "chart-wrap" });
    wrap.appendChild(svg);
    wrap.appendChild(tip);
    wrap.addEventListener("pointerleave", () => {
      tip.classList.add("hidden");
      markers.forEach((m) => m.remove());
      markers.length = 0;
    });

    /* 图例（点击可隐藏某条线） */
    const legend = ZC.el("div", { class: "legend" });
    const items = [...MODULES.map((k) => [k, SUBJECT_FULL[k]]), ["total", "总分"]];
    for (const [key, name] of items) {
      const btn = ZC.el("button", { class: `legend-item${hidden.has(key) ? " off" : ""}` });
      btn.innerHTML = `<i style="background:${ZC.COLORS[key]}"></i>${name}`;
      btn.onclick = () => {
        if (hidden.has(key)) hidden.delete(key);
        else hidden.add(key);
        render(host, data, { hidden });
      };
      legend.appendChild(btn);
    }

    host.appendChild(legend);
    host.appendChild(wrap);
    if (data.aggregate === "avg" && data.x_axis === "paper_year") {
      host.appendChild(ZC.el("p", { class: "hint" }, ["同一年份有多条记录，当前取平均值"]));
    }
  }

  return { render };
})();
