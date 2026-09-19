# AGENTS.md — 408 错题助手

> **实现现状（v0.2）**：项目已按「尽量轻量化」落地，功能覆盖：错题本（上传→切题→校正→勾选→Word）、
> 得分记录 + 趋势图 + 当前水平估计、用户系统（**用户端整体要登录**）、安卓 App。
> 实际技术栈与本文件第 3 节的原始设计**有出入**：
> SQLite 取代 MySQL、标准库 sqlite3 取代 SQLAlchemy、RapidOCR 取代 PaddleOCR、进程内任务队列取代 Celery、
> 管理后台用「预渲染页面 PNG + 拖拽层」取代 PDF.js、FastAPI 托管原生 H5 + 安卓 WebView 壳取代 UniApp、
> 趋势图自绘 SVG 取代 ECharts/uCharts、密码用标准库 pbkdf2 取代 passlib。
> **登录模型**：管理员不再是独立密钥，而是**普通账号**——用户名写进 `backend/.env` 的 `ZC_ADMIN_USERS` 即为管理员；
> `ZC_ADMIN_KEY` 只作兜底（脚本/首次引导）。登录态同时下发 HttpOnly Cookie（图片与下载链接靠它）。
> **以 README.md 第 1 节的对照表和 `backend/` 代码为准**；本文涉及处已加注。
> 第 5.7 / 12 节的「得分记录 + 趋势」已实现（实现说明见 7.8 / 12.7），另有用户系统（7.9）与安卓端（7.10）。

## 1. 项目简介

**408 错题助手**是一个面向考研 408 的错题整理工具。  
管理员上传历年真题 PDF（实际为扫描图片），系统自动裁剪出每道题，用户勾选错题后，按合理排版生成 Word 错题本。

核心目标：**把图片型 PDF 中的题目自动切出来，主观题也连续排，但尽量避免一道题跨页，输出可直接打印的 Word 文档。**

## 2. 核心需求

- 管理员上传历年真题 PDF（图片型，非文本型）。
- 自动识别题号（如 `6.`、`07.`、`43. (8分)`），按题号 y 坐标裁剪题目图片。
- 支持跨页题合并，支持主观题按小问再切分。
- 提供人工校正界面，允许拖拽调整题目边界、合并/拆分题目。
- 用户勾选错题，生成 Word 错题本。
- Word 排版规则：
  - 选择题连续排列，能放则放。
  - 主观题也连续排列，不强制单独起页。
  - **同一道题尽量不要跨页**：如果当前页剩余空间放不下整题，就换到下一页。
  - 如果一道题本身超过一页，按小问拆分；每个小问也尽量不跨页。
  - 标题与题目图片不分离（使用 `keepNext` / `keepLines`）。
- 输出文件命名：`408错题本_YYYY-MM-DD_HHMM.docx`。
- 用户可记录每次真题练习的各模块得分和总分，系统提供折线图展示历年真题得分趋势，X 轴可切换为“实际做题时间”或“真题年份”。

## 3. 技术栈

| 模块 | 技术 | v0.1 实际实现 |
|------|------|------|
| 后端 | Python 3.10+ / FastAPI | Python **3.12** + FastAPI（3.12 是 onnxruntime 的稳妥版本） |
| PDF 渲染 | PyMuPDF (fitz) | 同左（`import pymupdf as fitz`），zoom=3.0 ≈ 216 DPI |
| OCR | PaddleOCR（优先）或 RapidOCR | **RapidOCR (rapidocr-onnxruntime)**，离线、约 100MB |
| 图像处理 | OpenCV, Pillow | 同左（用 `opencv-python-headless`） |
| Word 生成 | python-docx | 同左 |
| 数据库 | MySQL + JSON 字段 | **SQLite 单文件** `data/db.sqlite`，标准库 sqlite3，JSON 存 TEXT |
| 管理后台（Web） | Vue 3 + PDF.js + Canvas（用于人工校正） | **Vue 3 + Vite**，叠加层直接盖在预渲染的页面 PNG 上（坐标零换算，省掉 pdfjs-dist） |
| 客户端 | UniApp（Vue 语法，跨端：H5 / 小程序 / App） | **原生 H5**（`web/`，无构建）+ **安卓 WebView 壳**（`android/`，手工打 APK）；要发小程序再套 UniApp |
| 图表 | ECharts / uCharts（UniApp 端推荐 uCharts） | **自绘 SVG**（`web/chart.js`）：双 Y 轴、悬浮详情、图例开关，零依赖 |
| 用户系统 | （原设计无） | 用户名 + 密码（`hashlib.pbkdf2_hmac`）+ 随机 token；**登录态同时下发 HttpOnly Cookie** |
| 鉴权 | （原设计无） | **三档**：公开 / 登录即可 / 管理员。管理员 = 用户名在 `backend/.env` 的 `ZC_ADMIN_USERS` 里（`users.is_admin`），`ZC_ADMIN_KEY` 仅兜底；默认拒绝 |
| 异步任务 | Celery / RQ（可选） | **不用**；`backend/tasks.py` 单线程队列 + 前端轮询 `papers.status/progress` |
| 文档转换 | LibreOffice headless（若需 docx → pdf） | 未使用，需复查分页时在 Word 里另存 PDF |
| 版本管理 | Git | 同左（已提供 `.gitignore`） |
| 部署 | Docker | 不用，本地 `start.bat` / uvicorn 直接跑 |

> **说明（v0.1 已按轻量化调整）**：  
> - 管理后台负责上传真题、人工校正题目边界：Vue 3 + Vite，叠加层画在**后端预渲染的页面 PNG** 上
>   （页面反正要渲染成 PNG 才能按像素裁题，直接复用可省掉 pdfjs-dist 这个 10MB 依赖，坐标也不用换算）。  
> - 用户端是 `web/` 下的原生 H5，由 FastAPI 托管在 `/`，手机浏览器直接用；要发小程序再套 UniApp。  
> - 三端（用户端 H5 / 管理后台 / 后端）互不混放：H5 不引入构建工具，管理后台不碰后端代码。

## 4. 系统架构

```
管理后台(Web，管理员账号登录) → 上传 PDF → FastAPI 后端（后台线程跑流水线）
→ 渲染每页为 PNG → OCR 识别题号 → 按 y 坐标切题
→ 跨页合并 → 人工校正 → 保存题目图片 + 元数据（SQLite）
→ 用户端 H5 / 安卓 App（**先登录**）→ 勾选错题 → 调用 FastAPI 生成 Word
→ 下载 408错题本_YYYY-MM-DD_HHMM.docx

用户端（登录态 = HttpOnly Cookie + Bearer token）→ 选真题年份 → 按题组录成绩 → FastAPI 算分
→ exam_records（SQLite）→ /api/scores/trend → 当前水平估计小表格 + 自绘 SVG 折线图
```

## 5. 数据处理流程（关键）

### 5.1 PDF 渲染
- 使用 `fitz.Matrix(3, 3)` 或更高（约 216 DPI）渲染每页为 PNG。
- 保存为 `page_{n}.png`。

### 5.2 OCR 题号识别
- 正则匹配题号：`^\s*[（(\[【]?\s*(\d{1,2})\s*[.、．。·・,，:：]\s*`（比原设计多兼容 `·，：` 等误识别）。
- 过滤范围：**1~47**（408 实际是 40 选择 + 7 综合，原设计的 45 偏小；范围在 `config.QNO_MAX` 可调）。
- 过滤条件（三道，缺一不可）：
  - 题号框必须落在页面左侧 `NUMBER_X_RATIO=0.25` 的列内（原设计写死 `x0 < 250`，按比例更耐换 DPI）；
  - OCR 置信度 ≥ `OCR_MIN_CONFIDENCE`；
  - **全卷题号严格单调递增**——这条最关键，图表里的数字、年份、公式编号都靠它排除，且允许中间漏识别。
- 记录每个题号的 `qno` 和 `y0`。
- 小问正则：`^\s*[（(]?\s*(\d{1,2})\s*[)）]\s*`（`1)` `（2）` 都认，`1.` 不认）。

### 5.3 按 y 坐标裁剪
- 同一页内，题号 `i` 的 `y0 - 15` 到题号 `i+1` 的 `y0 - 15` 为一道题。
- 最后一题裁到页底 `h - 3%`（去掉页脚）；若该区域是空白则**回收尾部空白行**（`trim_trailing_blank`），
  避免最后一道题在 Word 里白占半页。只切真空白行，不会丢内容。
- 上下留 15px 白边（`PAD_TOP_PX` / `PAD_BOTTOM_PX`）。
- 全卷用**统一的横向范围**（所有页墨迹列的并集）裁剪，保证每题图片物理缩放一致、字号统一。

### 5.4 跨页合并
- 若某页最后一题裁到页底，且下一页第一个题号之前有内容，则视为跨页题。
- 保存为多张图片：`2009_q43_p1.png`、`2009_q43_p2.png`。
- Word 中连续插入，中间不加分页符。

### 5.5 主观题小问切分
- 正则：`^\s*(\d+)\s*[)）]\s*`
- 找到小问的 y 坐标，将大题切成小问块，用于排版时按页分配。

### 5.6 人工校正
- 管理后台展示原 PDF 页面与识别出的题号列表。
- 支持拖拽调整上下边界、合并/拆分题块、手动补题号。
- 保存校正后的 bbox 和图片路径。

### 5.7 历年真题得分记录与趋势分析（**已实现**，见 7.8 / 12.7）
- 用户选真题年份 → 表单按题组填**答对个数**（1-11 DS、12-22 计组、23-32 OS、33-40 计网，每题 2 分）+
  综合题 41-47 逐题填**得分/满分**（各年分布不同，满分可改）→ 后端算模块分与总分入库。
- 趋势折线图 X 轴可切「实际做题时间 / 真题年份」，同年多条可取最近一次/平均/最高。

## 6. 目录结构（v0.2 实际）

```
408-wrong-cropper/
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example              # 复制成 backend/.env（配 ZC_ADMIN_USERS / ZC_PORT 等）
├── start.bat                 # 一键启动后端（python -m backend）
├── .gitignore
├── data/                     # 运行期数据，已 gitignore
│   ├── uploads/  pages/{paper_id}/  crops/{paper_id}/  exports/
│   └── db.sqlite             # papers/pages/questions/exports/users/sessions/exam_records/settings
├── backend/                  # FastAPI 后端
│   ├── main.py               # 入口：挂 /api、用户端 H5(/)、管理后台(/admin)、启动时生成密钥
│   ├── __main__.py           # python -m backend 启动（端口取 config.SERVER_PORT）
│   ├── config.py             # 全部可调参数 + 408 卷面结构 + 得分表单结构 + .env 读取
│   ├── auth.py               # 三档鉴权中间件（默认拒绝）
│   ├── db.py                 # SQLite schema 与访问层（取代 models.py + SQLAlchemy）
│   ├── schemas.py            # pydantic 请求模型
│   ├── tasks.py              # 进程内单线程任务队列（取代 Celery）
│   ├── services/
│   │   ├── pdf_render.py  ocr_question.py  cropper.py  pipeline.py
│   │   ├── layout.py  word_builder.py
│   │   ├── user_service.py   # 密码哈希 / token 签发校验
│   │   └── score_service.py  # 算分 / 入库 / 趋势聚合
│   └── api/
│       ├── papers.py  questions.py  export.py  common.py
│       ├── auth.py           # 注册/登录/登出/me/注册开关
│       ├── scores.py         # 得分录入/列表/编辑/删除/趋势/schema
│       └── admin.py          # 管理员：verify / settings / users
├── web/                      # 用户端 H5（原生 JS，无构建，取代 uniapp/）
│   ├── index.html            # 登录门 + 三个 Tab：选错题 / 得分 / 导出记录
│   ├── core.js               # 公共层：登录门(注册/登录/退出) / api(带 Cookie+token) / toast / Tab / DOM 工具
│   ├── app.js                # 选错题 + 导出记录
│   ├── scores.js             # 录入表单 + 记录列表 + 水平估计 + 趋势
│   ├── chart.js              # 自绘 SVG 折线图
│   └── style.css
├── admin/                    # 管理后台（Vue 3 + Vite）
│   ├── package.json  vite.config.js   # base=/admin/，dev 代理 /api -> 18100
│   └── src/{App.vue, api.js, style.css, components/{PageEditor,QuestionTable}.vue}
├── android/                  # 安卓端（Java WebView 壳，不用 Gradle）
│   ├── app/{AndroidManifest.xml, res/, java/com/zc/wrongbook/MainActivity.java}
│   ├── fetch_sdk.py          # 下载最小 SDK（platform + build-tools + R8）
│   ├── build_apk.py          # aapt2 → javac → d8 → zipalign → apksigner
│   └── dist/408错题本-1.0.apk
└── tests/
    ├── test_ocr_crop.py      # 题号正则 / 过滤 / 墨迹检测 / 卷面结构
    ├── test_layout.py        # 换页与拆分规则
    ├── test_scores.py        # 算分 / 趋势聚合 / 水平估计 / 密码与 token / 管理员标记
    ├── test_auth_admin.py    # 三档鉴权与权限边界（进程内跑 ASGI，不起端口）
    ├── web_dom_smoke.js      # 前端交互（迷你 DOM 跑真脚本：登录门/Tab/录入表单）
    ├── make_sample_pdf.py    # 生成图片型 PDF 测试样本
    ├── e2e.py                # 端到端（含 OCR）
    └── http_smoke.py         # HTTP 全链路（鉴权 + Cookie + 用户 + 错题 + 导出）
```

## 7. 关键模块说明

### 7.1 `backend/services/pdf_render.py`
- 输入 PDF，输出每页 PNG。
- 依赖：`PyMuPDF`。

### 7.2 `backend/services/ocr_question.py`
- 输入页面 PNG，输出题号列表 `[{qno, y0, x0, text}]`。
- 依赖：`PaddleOCR` 或 `RapidOCR`。
- 注意处理 OCR 误识别（如 `6。`、`07`）。

### 7.3 `backend/services/cropper.py`
- 输入页面 PNG 和题号列表，输出裁剪图片。
- 处理跨页逻辑。
- 依赖：`OpenCV`。

### 7.4 `backend/services/word_builder.py`
- 输入选中的题目列表，输出 Word。
- 页面设置：A4，边距 2cm。
- 图片宽度统一为 16cm，高度等比缩放。
- 使用 `python-docx` 的 `keepNext` / `keepLines` 防止标题与图片分离。

### 7.5 `backend/services/layout.py`
主观题连续排，但尽量避免一题跨页：
- 估算题块高度 = 各图片显示高度（等比缩放到 16cm 后）+ 段落间距 +（可选）标题 + 笔记区。
- 如果当前页剩余高度 ≥ 题块高度，直接放入当前页。
- 如果当前页剩余高度 < 题块高度：
  - 若题块高度 ≤ 一页可用高度，则换页后整题放入。
  - 若题块高度 > 一页可用高度，按小问拆分。
- 每个小问也尽量不跨页：小问能放当前页就放，放不下就换页。
- 选择题：能放则放，放不下才分页。
- 可用高度 = `CONTENT_H_CM(25.7cm) * LAYOUT_SAFETY_RATIO(0.96)`，留余量是因为 Word 分页不是像素级精确。
- **实现要点**：整题放得下时直接用已裁好的整块图（跨页题=多张块图一起贴），
  只有超过一页才 `segments()` 按小问切开、导出时现裁，避免生成一堆用不上的碎图。

### 7.6 `admin/` 管理后台（v0.1 实际）
- Vue 3 + Vite（**不用 PDF.js**）：页面图由后端渲染好，叠加层直接按同一像素坐标画在 `<img>` 上。
- `App.vue`：上传真题、真题列表、处理进度轮询、整卷重裁/重新识别/删除。
- `components/PageEditor.vue`：色块上下边缘拖拽改边界（可开「相邻题边界联动」），点色块选位置后切分。
- `components/QuestionTable.vue`：改题号/科目/题型、并入上一题、删除。
- 改边界后后端会自动重裁该题图片（`PATCH /api/questions/{id}` 带 `block`）。
- 开发态 `npm run dev`（5173，代理 `/api`）；`npm run build` 后由 FastAPI 挂在 `/admin/`。
- **注意**：`admin/dist` 是否存在是在 `main.py` import 时判断的，构建完要重启后端。

### 7.7 `web/` 用户端（v0.2 实际，取代 `uniapp/`）
- 原生 HTML/CSS/JS，**无构建步骤**，由 FastAPI 托管在 `/`，手机浏览器直接用；安卓 App 也是套这套页面。
- 文件分工：`core.js`（**登录门** + `api()` + toast + Tab 钩子 + DOM 小工具）、
  `app.js`（选错题 + 导出记录）、`scores.js`（录入表单 + 记录列表 + 水平估计 + 趋势）、`chart.js`（自绘 SVG）。
- **整个用户端都要登录**：`#gate`（登录/注册卡）与 `#shell`（三个 Tab）互斥显示；
  `core.js` 启动时先 `GET /api/auth/me`，401 就显示登录门，成功才 `showShell()` 并依次调用 `ZC.onEnter()` 注册的加载器。
- 三个 Tab：选错题 / 得分 / 导出记录；Tab 显示回调用 `ZC.onShow(view, fn)` 注册。
- 勾选状态存 `localStorage`；导出选项：显示标题、答题留白行数、图片宽度。
- 下载用 `<a download>` 触发（靠 Cookie 带登录态；安卓 WebView 里再由 `DownloadListener` + 系统下载器接管）。
- 任何请求 401 时 `core.js` 广播 `zc:need-login` → 弹回登录门（提示"登录已过期"）。

### 7.8 `backend/services/score_service.py` 与 `backend/api/scores.py`（**已实现**）
录入口径（表单结构由后端 `config.score_form_schema()` 给出，前端不写死）：
- **选择题**按 4 个题组填**答对个数**：1-11 DS、12-22 计组、23-32 OS、33-40 计网，每组每题 2 分（共 80 分）；
- **综合题 41-47** 逐题填**得分**与**满分**（各年分布不同，默认 41:10/42:13/43:13/44:10/45:7/46:8/47:9，共 70 分）；
- 模块分 = 该模块选择题得分 + 该模块综合题得分；总分 = 四模块之和（满分 150）。

数据表 `exam_records`：`user_id / paper_year / practice_date / total_score / ds_score / co_score /
os_score / cn_score / detail_json（录入口径 + 三处明细 + 当时的满分）/ note / created_at`。
存 `detail_json` 是为了以后改了默认满分，老记录不会被"重新解释"。

接口（都要登录，数据按 `user_id` 隔离）：
- `GET /api/scores/schema` 表单结构
- `POST /api/scores` 录入（算分后入库，返回带 `rates`/`total_rate` 的记录）
- `GET /api/scores` 列表（按做题时间倒序）
- `PUT /api/scores/{id}` / `DELETE /api/scores/{id}` 改（重算）/ 删
- `GET /api/scores/trend?x_axis=practice_date|paper_year&aggregate=latest|avg|max`

趋势返回：`labels` + `series`（ds/co/os/cn 为**百分比**、total 为**绝对分**）+ `points`（每个点带年份/日期/是否多月聚合）
\+ `estimate`（当前水平估计，见下）。
X 轴/聚合规则：
- `practice_date`：按 `practice_date` 升序，同一天多条按 `created_at` 升序，逐条出点；
- `paper_year`：按年份升序，同年多条按 `aggregate` 取 `latest`（默认）/`avg`/`max`，并在 `points[].count` 标出条数。

**当前水平估计（`score_service.estimate()`，接口里挂在 trend 响应的 `estimate` 字段）**
- 取最近 3 次成绩（按 `practice_date` 倒序），权重 `ESTIMATE_WEIGHTS = (0.5, 0.35, 0.15)`；
  **不足 3 次时按已有次数归一化**：1 次 = 100%，2 次 = 50/85≈58.8% 与 35/85≈41.2%（`weights` 字段给出实际权重，
  `base_weights` 是原始权重，`message` 会说明是否归一化）。
- **总分**：用**原始分**加权，满分固定 150（返回 `total` / `total_full` / `total_rate`）。
- **模块**：**先算每条记录的得分率再加权**，且分母用**那条记录当时的模块满分**（`_record_module_full` 从
  `detail_json.breakdown` 汇总各题组满分；老记录没明细时退回 `MODULE_FULL_SCORE`）。
  这样各年综合题分布不同（比如 42 题某年 13 分、某年 15 分）也不会引入偏差——测试里专门覆盖了
  "41 题满分改成 15 后 DS 应该是 100% 而不是 111%"。
- 它**始终按做题时间取最近三次**，与图表 X 轴切到 `paper_year` 无关（"当前水平"就是最近这几次的状态）。
- 前端只在曲线图**上方**渲染成一个小表格（`#estimate`，`web/scores.js` 的 `renderEstimate()`），不是图。

### 7.9 用户系统与鉴权（v0.2 新增）
- `backend/services/user_service.py`：`hash_password` / `verify_password`（`hashlib.pbkdf2_hmac`，20 万轮，随机盐，
  存成 `pbkdf2_sha256$迭代$salt$hash`）、`issue_token`（`secrets.token_urlsafe(32)` 存 `sessions` 表，默认 60 天）、
  `user_by_token`（顺带清理过期 token）、`revoke`、`is_admin` / `set_admin` / `sync_admin_users`。
- `backend/api/auth.py`：`register` / `login` / `logout` / `me` / `status`（后两个之外都公开）。
  **登录与注册会同时下发 HttpOnly Cookie `zc_token`**（`config.TOKEN_COOKIE`），退出时删掉。
- `backend/auth.py`（中间件，三档权限）：
  - 公开：`/api/health`、`/api/meta`、`/api/auth/status`、`POST /api/auth/login|register`；
  - 登录即可（`needs_login`）：`/api/catalog`、`/api/export(s)`、`/api/scores*`、`/api/auth/me|logout`，
    以及 **GET** `/api/questions*`（列表/详情/题目图）；
  - 其余 `/api/*`：管理员。管理员 = `users.is_admin`（由 `.env` 的 `ZC_ADMIN_USERS` 播种），或 `X-Admin-Key` 兜底。
  - 登录态取值顺序：**Cookie 优先，其次 `Authorization: Bearer`**（`token_from_request`）。
- **权限判断必须看「方法 + 路径」**：`/api/questions` 读是登录即可、改/删要管理员（详见 11 节的坑）。
- `backend/api/admin.py`：`GET|POST /api/admin/settings`（开关注册）、`GET|POST /api/admin/users`（建号/可指定管理员）、
  `PATCH /api/admin/users/{id}`（改管理员标记，不能取消自己）、`DELETE /api/admin/users/{id}`（连带记录与登录态）。
- 表：`users(id, username UNIQUE, password_hash, display_name, is_admin, created_at)`、
  `sessions(token PK, user_id, created_at, expires_at)`、`settings(key PK, value)`；
  `db.migrate()` 会给老库补 `is_admin` 列。
- **注意**：注册参数校验（用户名 2~24 位中英文/数字/下划线、密码 ≥6 位）都在 `user_service`，接口只翻译成 400。

### 7.10 `android/` 安卓端（v0.2 新增）
- 一个 `MainActivity`（Java，**零 androidx**）：顶部栏（标题/服务器/刷新）+ 进度条 + WebView + 错误横幅，
  界面全部代码搭，只有 `strings.xml` 一个资源。
- 服务器地址存 `SharedPreferences`，首次启动弹框让用户填；连不上时横幅提示并可重填。
- **登录态走 Cookie**：显式开 `CookieManager.setAcceptCookie` / `setAcceptThirdPartyCookies`，
  并在 `onPause()` 里 `flush()` 落盘，下次打开还是登录态。
- 导出 Word：拦截 `/api/exports/*/download` 与 WebView 的 `DownloadListener`，交给系统 `DownloadManager`
  存到公共「下载」目录（API < 29 时才申请 `WRITE_EXTERNAL_STORAGE`）。
- 顺带支持管理后台上传：实现 `onShowFileChooser` 选 PDF。
- `AndroidManifest.xml` 必须 `android:usesCleartextTraffic="true"`（局域网 http）。
- 打包**不用 Gradle**：`android/fetch_sdk.py` 拉最小 SDK，`android/build_apk.py` 走
  aapt2 compile/link → javac → jar → d8 → 自写 zip（`resources.arsc` 保持不压缩）→ zipalign → apksigner。
  产物 `android/dist/408错题本-1.0.apk`。

## 8. 开发环境与运行（v0.2 实际，Windows）

### 依赖安装（一次性）
用 **Python 3.12**（onnxruntime 对 3.13+ 支持滞后）。本机 py312 全局已有 fastapi/uvicorn/opencv/python-docx/numpy/pillow，
venv 用 `--system-site-packages` 复用它们，只装真正缺的包：

```powershell
& py -3.12 -m venv --system-site-packages .venv      # 没有 py 启动器就写 python3.12 的完整路径
.\.venv\Scripts\python.exe -m pip install pymupdf rapidocr-onnxruntime
# 必须：全局 starlette 1.3.0 与全局 fastapi 0.110.2 不兼容，装一个匹配版本盖住
.\.venv\Scripts\python.exe -m pip install "starlette==0.36.3"
```

### 配置管理员
```powershell
copy .env.example backend\.env
# ZC_ADMIN_USERS=你的用户名   ← 这个账号登录后就是管理员（逗号分隔可多个）
# ZC_ADMIN_KEY=              ← 留空则首次启动自动生成兜底密钥并打印
```

### 后端（同时托管用户端 H5 与管理后台）
```powershell
.\.venv\Scripts\python.exe -m backend        # 端口取 ZC_PORT，默认 18100
# 或双击 start.bat；要热重载：python -m backend --reload
```
- 用户端：<http://127.0.0.1:18100/> ｜ 管理后台：`/admin/` ｜ 接口文档：`/docs` ｜ 健康检查：`/api/health`

### 管理后台 Web
```powershell
cd admin
npm install --registry=https://registry.npmmirror.com     # 沙箱内需加 --cache ..\.npm-cache
npm run dev        # 5173，已配置 /api 代理到 18100
npm run build      # 产物 admin/dist，后端重启后由 /admin/ 托管
```

### 安卓端（可选，改动了 android/ 才需要）
```powershell
.\.venv\Scripts\python.exe android\fetch_sdk.py    # 首次：platform + build-tools + R8，约 130MB
.\.venv\Scripts\python.exe android\build_apk.py    # 产物 android/dist/408错题本-1.0.apk
```

### 测试
```powershell
.\.venv\Scripts\python.exe tests\test_ocr_crop.py    # 正则/过滤/裁剪，秒级
.\.venv\Scripts\python.exe tests\test_layout.py      # 换页/拆分规则，秒级
.\.venv\Scripts\python.exe tests\test_scores.py      # 算分/趋势/水平估计/密码与 token，秒级
.\.venv\Scripts\python.exe tests\test_auth_admin.py  # 鉴权三档与权限边界，秒级
node tests\web_dom_smoke.js                          # 前端交互（登录门/Tab/录入表单），秒级
.\.venv\Scripts\python.exe tests\e2e.py              # 端到端（含 OCR，约 1 分钟）
.\.venv\Scripts\python.exe tests\http_smoke.py       # HTTP 全链路（需先启动服务）
node --check web\scores.js                           # 前端改动后至少过一遍语法
```

## 9. 编码规范

- Python：PEP 8，类型注解，函数粒度小。
- 所有 OCR 和裁剪逻辑必须可单独测试。
- 图片路径使用相对路径，存储在 `data/` 下。
- 数据库记录包含：`year`, `subject`, `question_no`, `type`, `score`, `pages`, `bbox_json`, `image_paths`, `parent_id`, `order_no`。
- 生成 Word 后，建议同时导出 PDF 检查分页。
- 前端请求走统一封装：用户端 `web/app.js` 的 `api()`、管理后台 `admin/src/api.js` 的 `api`，统一抛错与提示。
- 遇到疑惑，先停止编码，询问清楚需求再继续编码。
- 积极写有用的、精简的文档，但不要什么都写，只写核心技术实现或者遇到的技术问题。
- 改动切题/排版逻辑后，跑 `tests/test_ocr_crop.py` + `tests/test_layout.py`；改动接口后跑 `tests/http_smoke.py` +
  `tests/test_scores.py`；改前端后至少 `node --check`。
- 趋势图是自绘 SVG（`web/chart.js`），别引 uCharts/ECharts；X 轴切换时重新请求 `/api/scores/trend`。

## 10. 给 AI 代理的指令

- **在修改代码前，请先阅读本文件。**
- **轻量优先**：新增依赖前先想清楚能不能用标准库或已有依赖替代。别引入 Poetry / SQLAlchemy / Celery /
  PaddleOCR / Element Plus 这类重家伙，除非确实需要（本项目刻意都避开了）。
- 用 Python 3.12 + `requirements.txt`，venv 用 `--system-site-packages` 复用已有包。
- 后端统一使用 FastAPI，接口遵循 RESTful 风格。
- 用户端是 `web/` 下的原生 H5，**不要引入仅浏览器可用的构建链**，也不要和管理后台代码混放。
- 管理后台用 Vue 3 + Vite，不引 UI 框架；拖拽叠加层直接画在预渲染的页面 PNG 上，不要引入 PDF.js 重新解析 PDF。
- 不要尝试从图片型 PDF 中直接提取文本，必须走 OCR + 裁剪图片路线。
- 所有自动切题结果必须支持人工校正，不要假设 OCR 100% 准确。
- 主观题可以连续排，不强制单独起页。
- 排版时优先保证“同一道题不跨页”；如果整题超过一页，再按小问拆分。
- 生成 Word 时，务必设置 `keepNext` 和 `keepLines`，避免标题与题目分离。
- 跨页题在 Word 中应连续插入多张图片，中间不要加分页符。
- 输出文件命名格式：`408错题本_YYYY-MM-DD_HHMM.docx`。
- 得分趋势：X 轴支持 `practice_date` 和 `paper_year`；模块用百分比、总分用绝对分（双 Y 轴）；同年多条给 `aggregate` 选项。
- 前端（H5 / 安卓）与后端的所有接口约定**以 `backend/api/` 为准**，改接口要同步 `web/core.js` 的封装和 `admin/src/api.js`。
- 安卓端不要引 androidx/Gradle：`android/` 刻意保持零依赖 + 手工流水线，加依赖会让 `build_apk.py` 失效。
- 密码/token/权限相关改动必须跑 `tests/test_auth_admin.py` + `tests/test_scores.py`；
  鉴权白名单在 `backend/auth.py`，**新增接口默认是要管理员**的；`needs_login()` 里加路径时注意别把管理接口放进去。
- 新增前端请求注意：浏览器侧登录态靠 Cookie（`<img>`、`<a download>` 带不了自定义头），
  fetch 要带 `credentials: "same-origin"`（`web/core.js` 的 `api()` 已经带上了）。
- 如果新增功能，请同步更新本 AGENTS.md 的对应章节。

## 11. 注意事项与坑

**切题相关**
- 图片型 PDF 没有文本层，`pdfplumber` 提取不到题号，必须用 OCR。
- OCR 可能把 `6.` 识别成 `6。`，正则要兼容；`07`、`43. (8分)` 也要认。
- 选项 `A. B. C. D.` 不会匹配数字题号；小问 `1)` `2)` 靠「分隔符是 `)` 而不是 `.`」+ 左侧 25% 列过滤，
  另有全卷题号单调递增兜底（图表数字、年份、公式编号都是这么被排除的）。
- 题号范围是 **1~47**，不是 45（AGENTS.md 早期写的 45 偏小）。
- 页眉页脚会干扰切题，按比例裁掉（页眉 2%、页脚 3%）。
- 跨页题判断：下一页第一个题号之前"有墨迹"才算跨页，靠 OpenCV 逐行墨迹检测，不靠 OCR。
- 人工合并两题、或把切开的题合回去时，同一页上首尾相接的块要合并成一块（`coalesce_blocks`），
  否则 Word 里会多出多余的碎图。
- Word 分页不是像素级精确，`layout.py` 只做估算（预留 4% 余量），生成后转 PDF 复查。
- 主观题答题区不要留太多空白，否则容易超出一页（默认 `note_lines=0`）。
- 主观题也连续排时，注意用"换页"（`w:pageBreakBefore`）而不是"分页符"来避免一题跨页。

**环境相关（都是实际踩过的）**
- `starlette` 1.x 与 `fastapi 0.110` 不兼容（`Router.__init__() got an unexpected keyword argument 'on_startup'`），
  venv 里必须钉 `starlette==0.36.3`；升级 fastapi 时两者要一起升。
- 本机 pip 直连 PyPI 只有几 KB/s，装大包（pymupdf/onnxruntime）要么等，要么用镜像：
  `-i http://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn`。
- npm 同理：`--registry=https://registry.npmmirror.com`；沙箱/受限环境下还要 `--cache <工作区内目录>`，
  否则写系统 npm-cache 会 EPERM。
- 这台机器的 `pwsh` 不在 PATH 里、实际执行的是 **Windows PowerShell 5.1**：`.ps1` 必须带 UTF-8 BOM（否则中文乱码报语法错），
  且没有 `Invoke-RestMethod -Form`。所以测试脚本一律用 Python 写（`tests/http_smoke.py`）。
- 控制台是 GBK：Python 脚本里加 `sys.stdout.reconfigure(encoding="utf-8")`，否则打印中文/符号会 `UnicodeEncodeError`。

**用户系统 / 得分相关**
- 新建 `users` / `exam_records` 这类表时**别忘 `created_at`**：schema 没写默认值，insert 漏字段会
  `NOT NULL constraint failed`（用户系统第一次就是这么炸的）。
- `practice_date` 和 `paper_year` 必须分开存，不要混用；日期统一 `YYYY-MM-DD`。
- 按 `paper_year` 聚合时，同年多条要明确口径（`latest` / `avg` / `max`），并在返回里标出 `count`。
- 录入口径与满分要跟着记录一起存（`detail_json`）：以后改了默认满分，老记录不能被重新解释。
- 用户接口不吃管理员密钥：`/api/scores*` 只认登录态（Cookie/token）；反过来管理接口的 `X-Admin-Key` 也进不了用户接口。
- **浏览器端必须有 Cookie**：`<img src="/api/questions/1/images/0">` 和 `<a download>` 带不了 `Authorization` 头，
  只靠 token 会看到一片裂图、下载 401。所以就绪时同时下发 HttpOnly Cookie（`zc_token`），
  fetch 用 `credentials: "same-origin"`；安卓 WebView 记得开 `CookieManager` 并在 onPause 里 flush。
- **权限判断别只看路径前缀**：`/api/questions` 前缀下面既有"读"（登录即可）又有"改/删"（管理员），
  第一版按前缀一刀切，导致**任何登录用户都能 PATCH/DELETE 题目**（`tests/test_auth_admin.py` 抓出来的）。
  现在 `needs_login()` 是「方法 + 路径」。
- `403` 与 `401` 要分清：没登录/没凭证 → 401（前端弹登录页）；已登录但不是管理员 → 403（提示换账号）。

**前端交互相关（v0.2 踩过）**
- `core.js` 的 `initTabs()` 一度"只定义没调用"，结果**三个 Tab 全都没点击事件**（用户点了没反应），
  而后端 6 个测试套件照样全绿——这类 bug 只有真跑前端才看得见。
  现在有 `tests/web_dom_smoke.js`：自带迷你 DOM，在 Node 里跑真实的 `core.js/app.js/scores.js/chart.js`，
  断言登录门、Tab 切换、录入表单展开。**改前端交互后必须跑它**；把 `initTabs()` 注释掉会立刻 6 条 FAIL。
- 迷你 DOM 只实现 `#id` / `.class` 选择器和用到的那批 API；前端若用了新的 DOM API
  （如 `createDocumentFragment`、`insertAdjacentHTML`、`el.id`），要在测试脚手架里补上，否则会"因为脚手架缺失"而失败。
- 用户端关键入口（登录门 → 得分 Tab → 「录入成绩」）都在这个测试里断言过，别再漏。

**安卓相关（实际踩过的）**
- build-tools 34 自带 d8 是 R8 8.2.2，遇到 **JDK 21** 编译出的匿名内部类会崩
  （`NullPointerException: Cannot invoke "String.length()" because "<parameter1>" is null`）：
  要么下新版 `r8.jar`（`fetch_sdk.py` 已经这么干），要么 javac 加 `-g:none`。
- 手工打包 APK 时 `resources.arsc` **必须不压缩**（`ZIP_STORED`），否则 targetSdk 30+ 在 Android 11 上装不上。
- WebView 必须开 `android:usesCleartextTraffic="true"`，否则 Android 9+ 直接拦掉局域网 http。
- `<a download>` 在 WebView 里不一定触发下载，所以额外拦了 `/api/exports/*/download` 交给系统下载器。
- 腾讯镜像 `mirrors.cloud.tencent.com/AndroidSDK/` 有 platform/build-tools（platform 要按
  `repository2-3.xml` 里的真实文件名，是 `platform-34-ext7_r03.zip` 这种）。

## 12. 历年真题得分情况详细设计（**已实现**）

> 本节保留最初的设计稿，**实际实现见 7.8 / 12.7**（几处与原稿不同：录入按题组填答对个数而不是直接填模块分；
> 前端是 `web/scores.js` + 自绘 SVG，不是 UniApp + uCharts；数据表在 SQLite 里，
> `AUTO_INCREMENT` 换成 `INTEGER PRIMARY KEY AUTOINCREMENT`、`DECIMAL(5,1)` 用 `REAL`、多了 `detail_json`）。

### 12.1 功能概述
用户每次完成一套 408 真题后，录入各模块得分和总分。系统保存记录，并提供折线图展示趋势。  
X 轴可切换：
- **按实际做题时间**：例如 9.17 做 2009，9.21 做 2011，9.25 做 2010，则 X 轴为 9.17、9.21、9.25。
- **按真题年份**：则 X 轴为 2009、2010、2011。

折线图包含 5 条线：DS、计组、OS、计网（得分比）和总分。

### 12.2 数据模型
```sql
CREATE TABLE exam_records (
  id INT PRIMARY KEY AUTO_INCREMENT,
  user_id INT NOT NULL,
  paper_year INT NOT NULL,
  practice_date DATE NOT NULL,
  total_score DECIMAL(5,1) NOT NULL,
  ds_score DECIMAL(5,1) NOT NULL,
  co_score DECIMAL(5,1) NOT NULL,
  os_score DECIMAL(5,1) NOT NULL,
  cn_score DECIMAL(5,1) NOT NULL,
  note TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id),
  INDEX idx_practice_date (practice_date),
  INDEX idx_paper_year (paper_year)
);
```

满分常量：
- 总分：150
- DS：45
- 计组：45
- OS：35
- 计网：25

### 12.3 API 设计
- `POST /api/scores`  
  请求体：
  ```json
  {
    "paper_year": 2009,
    "practice_date": "2026-09-17",
    "total_score": 110,
    "ds_score": 36,
    "co_score": 32,
    "os_score": 26,
    "cn_score": 16,
    "note": "第一次做"
  }
  ```
  响应：创建成功返回记录 ID。

- `GET /api/scores`  
  返回当前用户所有得分记录，按 `practice_date` 降序。

- `GET /api/scores/trend?x_axis=practice_date`  
  返回折线图数据：
  ```json
  {
    "x_axis": "practice_date",
    "labels": ["2026-09-17", "2026-09-21", "2026-09-25"],
    "series": {
      "ds": [80.0, 75.6, 82.2],
      "co": [71.1, 72.2, 77.8],
      "os": [74.3, 80.0, 78.6],
      "cn": [64.0, 65.0, 70.0],
      "total": [110, 112, 115]
    }
  }
  ```
  其中模块值为百分比（得分 / 满分 * 100），总分为原始分。

### 12.4 前端页面（原设计是 UniApp，实际是 `web/scores.js`）
- 得分页：登录/注册卡 → 记录列表（`recordList`，可编辑/删除）→ 趋势图（`#chart`）+ X 轴/聚合下拉。
- 录入表单由 `/api/scores/schema` 动态生成：4 个选择题组（填答对个数）+ 7 道综合题（填得分/满分），
  输入时**前端实时算分**（口径与后端 `score_service.compute` 一致），保存时把原始录入值提交给后端复算。
- 图表 `web/chart.js`：双 Y 轴（左 0~100% 模块得分率、右 0~150 总分）、悬浮/触摸看当次明细、图例点击隐藏某条线。

### 12.5 图表配置要点
- X 轴：`labels` 数组。
- Y 轴左：百分比 0~100%。
- Y 轴右：总分 0~150。
- 系列：
  - `ds`、`co`、`os`、`cn` 为百分比，左轴。
  - `total` 为绝对分，右轴。
- 颜色区分：DS 蓝、计组绿、OS 橙、计网紫、总分红。
- 空数据时显示“暂无记录，请先添加”。

### 12.6 排序与聚合规则
- `x_axis=practice_date`：按 `practice_date` 升序。同一天多条记录按 `created_at` 升序。
- `x_axis=paper_year`：按 `paper_year` 升序。同一年份多条记录默认取最近一次（`created_at` 最大），未来可扩展为平均/最高。
- 如果用户未记录某年，则该年不出现在 X 轴（或补 0，根据产品决定，默认不补）。

### 12.7 v0.2 实际实现对照（与上面设计稿的差异）
- **录入**：不是直接填模块分，而是"选年份 → 按题组填答对个数（选择题）+ 逐题填得分/满分（41-47）→ 后端算分"。
  表单结构由 `GET /api/scores/schema` 给（`config.score_form_schema()`），前端只渲染；
  `POST/PUT` 提交的是原始录入值，后端 `score_service.compute()` 复算，防止前端算错或改分。
- **存库**：`exam_records` 多了 `detail_json`（原始录入 + 三处明细 + 当时的满分），少了个 `note` 之外的一切冗余。
- **接口**：见 7.8；趋势接口多了 `aggregate` 参数与 `points` 明细数组。
- **前端**：`web/scores.js`（登录/表单/列表）+ `web/chart.js`（自绘 SVG），没有 UniApp、没有 uCharts。
- **用户**：整个用户端都要登录（题目/导出/得分都在登录门后），得分记录按 `user_id` 隔离，
  没人能看到别人的成绩（`tests/test_auth_admin.py` / `test_scores.py` 有覆盖）。
- **当前水平估计**（v0.2 追加）：最近三次 50/35/15 加权，不足三次归一化；总分用原始分、模块用各自当时的满分算得分率；
  挂在 trend 响应的 `estimate` 上，前端只画成曲线图上方的小表格。实现与坑见 7.8。

## 13. 未来扩展

- 自动识别题目类型（选择题/主观题）。
- 自动提取题目文本（OCR + 公式识别），支持搜索。
- 用户错题本云端同步。
- 支持导出 PDF、Anki 卡片。
- 多科目支持（数学、英语等）。
- 得分趋势支持按模块查看历史最高/最低/平均。
- 支持导入/导出得分记录 CSV。
- 错题勾选记录云端同步（当前刻意只存浏览器本地：用户明确说了"不需要在线错题本"）。
- 试卷结构按年份可配置（目前综合题满分在录入表单里手改）。
- 管理员重置用户密码 / 用户改密码。

