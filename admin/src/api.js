/**
 * 统一 API 封装：相对路径直接怼 FastAPI，错误统一抛出。
 *
 * 管理后台现在**用普通用户账号登录**（用户名在 backend/.env 的 ZC_ADMIN_USERS 里即管理员），
 * 所以这里和用户端共用同一个 token（localStorage 的 zc.token）——
 * 在一个浏览器里登录一次，用户端和管理后台都是登录态。
 * 另外保留兜底密钥（X-Admin-Key），脚本或"还没有管理员账号"时用。
 */
const KEY_STORAGE = "zc.adminKey";
const TOKEN_STORAGE = "zc.token";

export const getAdminKey = () => localStorage.getItem(KEY_STORAGE) || "";
export const getUserToken = () => localStorage.getItem(TOKEN_STORAGE) || "";

export function setAdminKey(value) {
  if (value) localStorage.setItem(KEY_STORAGE, value);
  else localStorage.removeItem(KEY_STORAGE);
}

export function setUserToken(value) {
  if (value) localStorage.setItem(TOKEN_STORAGE, value);
  else localStorage.removeItem(TOKEN_STORAGE);
}

export function clearAuth() {
  setAdminKey("");
  setUserToken("");
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const key = getAdminKey();
  if (key) headers["X-Admin-Key"] = key;
  const token = getUserToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch (_) {
      /* 非 JSON 错误体 */
    }
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("zc:admin-locked"));
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  meta: () => request("/api/meta"),
  verify: () => request("/api/admin/verify"),

  login: (username, password) =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request("/api/auth/logout", { method: "POST" }),

  getSettings: () => request("/api/admin/settings"),
  setSettings: (payload) => request("/api/admin/settings", { method: "POST", body: JSON.stringify(payload) }),
  listUsers: () => request("/api/admin/users"),
  createUser: (payload) => request("/api/admin/users", { method: "POST", body: JSON.stringify(payload) }),
  patchUser: (id, payload) => request(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteUser: (id) => request(`/api/admin/users/${id}`, { method: "DELETE" }),

  listPapers: () => request("/api/papers"),
  getPaper: (id) => request(`/api/papers/${id}`),
  deletePaper: (id) => request(`/api/papers/${id}`, { method: "DELETE" }),
  reprocess: (id) => request(`/api/papers/${id}/reprocess`, { method: "POST" }),
  recrop: (id) => request(`/api/papers/${id}/recrop`, { method: "POST" }),

  getStructure: (id) => request(`/api/papers/${id}/structure`),
  saveStructure: (id, payload) =>
    request(`/api/papers/${id}/structure`, { method: "PUT", body: JSON.stringify(payload) }),
  rebuildStructure: (id) => request(`/api/papers/${id}/structure/rebuild`, { method: "POST" }),

  upload(file, year, title) {
    const form = new FormData();
    form.append("file", file);
    form.append("year", String(year));
    form.append("title", title || "");
    return request("/api/papers", { method: "POST", body: form });
  },

  patchQuestion(id, patch) {
    return request(`/api/questions/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  },
  deleteQuestion: (id) => request(`/api/questions/${id}`, { method: "DELETE" }),
  split: (paperId, pageNo, y) =>
    request(`/api/papers/${paperId}/questions/split`, {
      method: "POST",
      body: JSON.stringify({ page_no: pageNo, y }),
    }),
  merge: (paperId, keepId, dropId) =>
    request(`/api/papers/${paperId}/questions/merge`, {
      method: "POST",
      body: JSON.stringify({ keep_id: keepId, drop_id: dropId }),
    }),
};
