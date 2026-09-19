/* 公共层：登录门、请求封装（Cookie + token）、提示、Tab 切换、小工具。无构建，纯原生。 */
window.ZC = (() => {
  "use strict";

  const TOKEN_KEY = "zc.token";
  const MODULE_KEYS = ["ds", "co", "os", "cn"];
  const SUBJECTS = { ds: "数据结构", co: "计组", os: "操作系统", cn: "计网" };
  const SUBJECT_FULL = { ds: "数据结构", co: "计算机组成原理", os: "操作系统", cn: "计算机网络" };
  const COLORS = { ds: "#2f6fed", co: "#22a06b", os: "#e08600", cn: "#8b5cf6", total: "#e5484d" };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const token = () => localStorage.getItem(TOKEN_KEY) || "";
  const setToken = (value) => {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  };

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const t = token();
    if (t) headers.Authorization = `Bearer ${t}`;
    // 登录态主要靠 HttpOnly Cookie（图片/下载链接带不了自定义头），token 头作为补充
    const res = await fetch(path, { credentials: "same-origin", ...options, headers });
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("zc:need-login"));
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      } catch (_) {
        /* 非 JSON 错误体 */
      }
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return res.status === 204 ? null : res.json();
  }

  function toast(message, isError = false) {
    const node = $("#toast");
    if (!node) return;
    node.textContent = message;
    node.className = `toast${isError ? " err" : ""}`;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => node.classList.add("hidden"), 2800);
  }

  /* 安卓壳（MainActivity 往 UA 里加了 ZCWrongBook/1.0）或手机浏览器：
     它们的「下载」最不靠谱（WebView 拦下载、手机浏览器没反应、外部浏览器又没有登录 Cookie），
     所以干脆跟后端要一条 15 分钟的临时链接，弹面板让用户复制 / 用浏览器打开。 */
  function isAndroidShell() {
    return typeof navigator !== "undefined" && /ZCWrongBook/.test(navigator.userAgent || "");
  }

  function isMobile() {
    return isAndroidShell() || (typeof navigator !== "undefined" && /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent || ""));
  }

  /** 复制文本：安全上下文用 clipboard API，安卓壳走原生桥，再退回 execCommand。 */
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext !== false) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {
      /* 落下去走别的办法 */
    }
    try {
      if (window.ZCAndroid && typeof window.ZCAndroid.copy === "function") {
        window.ZCAndroid.copy(text);
        return true;
      }
    } catch (_) {
      /* 没有原生桥就算了 */
    }
    try {
      const box = document.createElement("textarea");
      box.value = text;
      box.setAttribute("readonly", "readonly");
      box.style.position = "fixed";
      box.style.opacity = "0";
      document.body.appendChild(box);
      box.select();
      const ok = document.execCommand("copy");
      box.remove();
      return Boolean(ok);
    } catch (_) {
      return false;
    }
  }

  /** 弹出下载链接面板（URL 已选中，方便长按复制） */
  function showDownloadPanel(url, hint) {
    const panel = $("#dlPanel");
    const input = $("#dlUrl");
    if (!panel || !input) {
      toast("下载链接：" + url);
      return;
    }
    input.value = url;
    if (hint) $("#dlHint").textContent = hint;
    panel.classList.remove("hidden");
    try {
      input.focus();
      input.select();
    } catch (_) {
      /* 有的 WebView 不让脚本选中，问题不大 */
    }
  }

  function hideDownloadPanel() {
    const panel = $("#dlPanel");
    if (panel) panel.classList.add("hidden");
  }

  /** 用登录态换一条 15 分钟的临时下载链接 */
  async function tempDownloadUrl(downloadUrl) {
    const ticketUrl = downloadUrl.replace(/\/download(\?.*)?$/, "/ticket");
    const data = await api(ticketUrl, { method: "POST" });
    return new URL(data.url, location.origin || window.location.href).href;
  }

  /** 弹面板的时机：**只认安卓壳**（web 端行为保持原样：桌面 / 手机浏览器都还是直接下载）。
      如果以后想让手机浏览器也走「复制链接」，把这里换成 isMobile() 即可。 */
  function needCopyPanel() {
    return isAndroidShell();
  }

  async function download(url) {
    if (needCopyPanel() && url.includes("/download")) {
      try {
        const temp = await tempDownloadUrl(url);
        showDownloadPanel(temp);
        // 能直接交给外部浏览器就顺手打开一次（安卓壳会拦这个 URL 用系统浏览器打开）
        location.href = temp;
      } catch (err) {
        toast(`取下载链接失败：${err.message}`, true);
      }
      return;
    }
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  /** 面板上的两个按钮（core.js 启动时调一次） */
  function initDownloadPanel() {
    const copyBtn = $("#dlCopy");
    const openBtn = $("#dlOpen");
    const closeBtn = $("#dlClose");
    if (copyBtn) {
      copyBtn.onclick = async () => {
        const url = $("#dlUrl").value;
        const ok = await copyText(url);
        $("#dlHint").textContent = ok
          ? "已复制：粘到浏览器地址栏回车就能下载（15 分钟内有效）。"
          : "复制失败：长按上面的输入框手动全选复制（15 分钟内有效）。";
      };
    }
    if (openBtn) openBtn.onclick = () => { location.href = $("#dlUrl").value; };
    if (closeBtn) closeBtn.onclick = hideDownloadPanel;
  }

  /** 极简 DOM 构造：el("div", {class:"x"}, ["文本", node]) */
  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key === "html") node.innerHTML = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (value !== null && value !== undefined) node.setAttribute(key, value);
    }
    for (const child of [].concat(children)) {
      if (child === null || child === undefined) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const esc = (text) =>
    String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const round1 = (value) => Math.round(Number(value) * 10) / 10;

  /* ---------------------------------------------------------- Tab */
  const hooks = {};
  const onShow = (view, fn) => (hooks[view] = fn);

  function initTabs() {
    $$(".tab").forEach((tab) => {
      tab.onclick = () => {
        $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
        const view = tab.dataset.view;
        $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${view}`));
        const bar = $(".bottombar");
        if (bar) bar.classList.toggle("hidden", view !== "pick");
        if (hooks[view]) hooks[view]();
      };
    });
  }

  /* ---------------------------------------------------------- 登录门 */
  const enterHooks = [];
  const onEnter = (fn) => enterHooks.push(fn);
  let currentUser = null;
  let allowRegister = true;
  let authMode = "login";

  function setAuthMode(mode) {
    authMode = mode;
    $("#authTabLogin").classList.toggle("active", mode === "login");
    $("#authTabRegister").classList.toggle("active", mode === "register");
    $("#authName").classList.toggle("hidden", mode !== "register");
    $("#authSubmit").textContent = mode === "login" ? "登录" : "注册并登录";
    $("#authMsg").textContent = "";
  }

  function showGate(message = "") {
    $("#gate").classList.remove("hidden");
    $("#shell").classList.add("hidden");
    const hint = $("#gateHint");
    if (hint) {
      hint.textContent = message || "登录后即可看题、勾选错题、生成 Word，并保存自己的得分记录。";
    }
  }

  function showShell() {
    $("#gate").classList.add("hidden");
    $("#shell").classList.remove("hidden");
  }

  async function loadAuthStatus() {
    try {
      const info = await api("/api/auth/status");
      allowRegister = info.allow_register;
      $("#authTabRegister").classList.toggle("hidden", !allowRegister);
      if (!allowRegister && authMode === "register") setAuthMode("login");
    } catch {
      /* 服务没起来时先不管 */
    }
  }

  async function submitAuth() {
    const username = $("#authUser").value.trim();
    const password = $("#authPass").value;
    const displayName = $("#authName").value.trim();
    if (!username || !password) {
      $("#authMsg").textContent = "用户名和密码都要填";
      return;
    }
    const button = $("#authSubmit");
    button.disabled = true;
    try {
      const body = authMode === "login" ? { username, password } : { username, password, display_name: displayName };
      const data = await api(authMode === "login" ? "/api/auth/login" : "/api/auth/register", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setToken(data.token);
      $("#authPass").value = "";
      toast(`${authMode === "login" ? "欢迎回来" : "注册成功"}，${data.user.display_name}`);
      await start();
    } catch (err) {
      $("#authMsg").textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {
      /* 退出失败也无所谓，本地清掉就行 */
    }
    setToken("");
    currentUser = null;
    showGate("已退出登录");
  }

  async function start() {
    try {
      const data = await api("/api/auth/me");
      currentUser = data.user;
      allowRegister = data.allow_register;
      $("#authTabRegister").classList.toggle("hidden", !allowRegister);
      showShell();
      for (const hook of enterHooks) {
        try {
          await hook(currentUser);
        } catch (err) {
          toast(`加载失败：${err.message}`, true);
        }
      }
    } catch (err) {
      currentUser = null;
      if (err.status === 401) {
        showGate();
        await loadAuthStatus();
      } else {
        showGate(`连不上后端：${err.message}`);
      }
    }
  }

  function wireGate() {
    initTabs(); // Tab 的点击绑定（漏了这行会导致点 Tab 没反应）
    initDownloadPanel(); // 手机端「复制下载链接」面板的按钮
    $("#authTabLogin").onclick = () => setAuthMode("login");
    $("#authTabRegister").onclick = () => setAuthMode("register");
    $("#authSubmit").onclick = submitAuth;
    $("#authPass").addEventListener("keyup", (event) => {
      if (event.key === "Enter") submitAuth();
    });
    window.addEventListener("zc:need-login", () => {
      if (!currentUser) return; // 已经在登录页就别重复弹提示
      currentUser = null;
      showGate("登录已过期，请重新登录");
    });
    const kick = () => start();
    if (document.readyState === "complete") kick();
    else window.addEventListener("load", kick);
  }

  wireGate();

  return {
    $,
    $$,
    api,
    toast,
    download,
    el,
    esc,
    round1,
    token,
    setToken,
    initTabs,
    onShow,
    onEnter,
    logout,
    session: { start, logout, user: () => currentUser },
    MODULE_KEYS,
    SUBJECTS,
    SUBJECT_FULL,
    COLORS,
  };
})();
