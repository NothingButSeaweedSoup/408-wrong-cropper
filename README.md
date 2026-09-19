# 408 错题助手

面向考研 408 的错题整理与成绩跟踪工具：

1. **错题本**：管理员上传图片型真题 PDF → 自动 OCR 题号并切题 → 人工拖拽校正 → 用户勾选错题 → 生成可直接打印的 Word 错题本。
2. **得分记录与趋势**：用户选真题年份 → 按**那份试卷的卷面结构**填答对个数 / 综合题填得分 → 系统算分入库 →
   折线图看趋势（X 轴可切「做题时间 / 真题年份」）。
   曲线图上方还有一个**当前水平估计**小表格：最近三次成绩按 50% / 35% / 15% 加权（不足三次按比例归一化）。
   各年 408 分布不一样（DS 选择题某些年 10 题、综合题分值年年不同），所以**题号范围与分值挂在试卷上**，
   管理员可在后台「试卷结构」里改。
3. **用户系统**：注册 / 登录后使用；**用户端整个要登录**（看题、导出、得分都按账号走），
   管理后台也用同一套账号登录，**用户名写在 `.env` 的 `ZC_ADMIN_USERS` 里就是管理员**。
4. **三端**：用户端 H5（手机浏览器）、管理后台（Vue 3）、**安卓 App**（WebView 壳，APK 可直接安装）。

设计目标是**尽量轻**：一个 Python 进程 + 一个 SQLite 文件 + 免构建的前端，没有 MySQL、没有 Celery、没有 PaddleOCR、没有 Gradle；
要上服务器也有现成的 `Dockerfile` + `docker-compose.yml`（见第 12 节），一个容器一个卷就够。

---

## 1. 轻量化取舍

| 原始设想 | 本实现 | 为什么 |
|---|---|---|
| MySQL + JSON 字段 | **SQLite 单文件** `data/db.sqlite` | 单机、几千条记录，零安装零服务 |
| SQLAlchemy ORM | **标准库 sqlite3** | 十几条 SQL，省一层依赖 |
| PaddleOCR | **RapidOCR (onnxruntime 移动模型)** | PaddlePaddle 是 GB 级依赖；RapidOCR 约 100MB 且离线可用 |
| Celery / RQ | **进程内单线程队列**（`backend/tasks.py`） | 单机串行足够，前端轮询进度 |
| 管理后台 Vue3 + PDF.js | **Vue3 + Vite + 预渲染页面 PNG** | 页面本来就要渲染成 PNG 才能按像素裁题，叠加层直接复用，坐标零换算，省掉 pdfjs-dist |
| UniApp 客户端 | **原生 H5 + 安卓 WebView 壳** | 免 HBuilderX 构建链；安卓端用系统 API 手打 APK，不引 androidx/Gradle |
| passlib / PyJWT | **标准库 hashlib.pbkdf2_hmac + 随机 token 存库** | 单机场景够用，省两个依赖 |
| ECharts / uCharts | **自绘 SVG 折线图**（`web/chart.js`，约 200 行） | 离线可用、几 KB、和页面风格统一 |
| LibreOffice / Docker | 不用 LibreOffice；**Docker 只作为部署方式**（多阶段构建，见第 12 节） | 需复查分页时在 Word 里另存 PDF；容器里跑的是同一个 `python -m backend` |

---

## 2. 目录结构

```
408-wrong-cropper/
├── backend/
│   ├── main.py               # 入口：/api、用户端 H5(/)、管理后台(/admin)
│   ├── config.py             # 全部可调参数 + 408 默认卷面结构（兜底用）
│   ├── db.py                 # SQLite schema（papers/pages/questions/exports/users/sessions/exam_records/settings）
│   ├── auth.py               # 三档鉴权中间件：公开 / 用户 token / 管理员密钥
│   ├── tasks.py              # 单线程后台任务队列
│   ├── api/
│   │   ├── papers.py         # 上传 / 列表 / 详情 / 页面图 / 重识别 / 删除 / **卷面结构(读改重建)**
│   │   ├── questions.py      # 人工校正：改边界(自动重裁) / 合并 / 拆分 / 整卷重裁
│   │   ├── export.py         # 生成 Word、导出历史、下载
│   │   ├── auth.py           # 注册 / 登录 / 登出 / me / 注册开关状态
│   │   ├── scores.py         # 得分录入 / 列表 / 编辑 / 删除 / 趋势 / 表单结构
│   │   └── admin.py          # 管理员：校验密钥、注册开关、用户管理
│   └── services/
│       ├── pdf_render.py     # PyMuPDF 渲染（216 DPI）
│       ├── ocr_question.py   # RapidOCR 题号识别 + 正则/位置/单调递增过滤
│       ├── cropper.py        # 按 y 切题、跨页合并、空白回收、碎块合并、重裁
│       ├── pipeline.py       # 渲染→OCR→切题→入库
│       ├── layout.py         # 排版规则（整题不跨页 / 超页按小问拆）
│       ├── word_builder.py   # python-docx 输出
│       ├── user_service.py   # 密码哈希、token 签发与校验
│       ├── paper_structure.py # **每份试卷自己的题号范围与分值**（自动推导/校验/查库）
│       └── score_service.py  # 算分、入库、趋势聚合
├── web/                      # 用户端 H5（无构建）
│   ├── index.html            # 三个 Tab：选错题 / 得分 / 导出记录
│   ├── core.js               # 公共层：请求(带 token)、提示、Tab、DOM 小工具
│   ├── app.js                # 选错题 + 导出记录
│   ├── scores.js             # 登录注册 + 得分录入表单 + 记录列表
│   ├── chart.js              # 自绘 SVG 折线图（双 Y 轴、悬浮详情、图例开关）
│   └── style.css
├── admin/                    # 管理后台（Vue 3 + Vite）
│   └── src/{App.vue, api.js, style.css, components/{PageEditor,QuestionTable,StructurePanel}.vue}
├── android/                  # 安卓端（Java WebView 壳）
│   ├── app/AndroidManifest.xml, app/res/, app/java/com/zc/wrongbook/MainActivity.java
│   ├── fetch_sdk.py          # 下载最小 Android SDK（platform + build-tools + R8）
│   ├── build_apk.py          # 手工流水线打 APK（aapt2 → javac → d8 → zipalign → apksigner）
│   └── dist/408错题本-1.1.apk
├── data/                     # 运行期数据（uploads/pages/crops/exports/db.sqlite）
├── tests/
│   ├── test_ocr_crop.py      # 题号正则 / 过滤 / 墨迹检测 / 卷面结构
│   ├── test_layout.py        # 换页与拆分规则
│   ├── test_scores.py        # 算分 / 趋势聚合 / 密码与 token
│   ├── test_structure.py     # 卷面结构：自动推导 / 校验 / 按结构算分 / OCR 读分值
│   ├── make_sample_pdf.py    # 生成图片型 PDF 测试样本
│   ├── e2e.py                # 端到端（含 OCR）
│   └── http_smoke.py         # HTTP 全链路（鉴权 + 用户 + 错题 + 导出）
├── requirements.txt
├── Dockerfile                # 多阶段：node 构建管理后台 → python:3.12-slim 跑后端
├── docker-compose.yml        # 单机部署（18100 端口 + ./data 卷）
├── .dockerignore
├── .env.example              # 复制成 backend/.env 用
└── start.bat                 # 一键启动后端（本地开发）
```

---

## 3. 环境准备

后端用 **Python 3.12**（onnxruntime 对 3.13+ 支持滞后）。本机 py312 全局已有 fastapi/uvicorn/opencv/python-docx/numpy/pillow，
因此 venv 用 `--system-site-packages` 复用它们，只额外装真正缺的包：

```powershell
```powershell
py -3.12 -m venv --system-site-packages .venv      # 没有 py 启动器就写 python3.12 的完整路径
.\.venv\Scripts\python.exe -m pip install pymupdf rapidocr-onnxruntime "starlette==0.36.3"
```
```

> **坑 1**：py312 全局 `starlette 1.3.0` 与全局 `fastapi 0.110.2` 不兼容
> （`Router.__init__() got an unexpected keyword argument 'on_startup'`），必须装 `starlette==0.36.3` 盖住。
> **坑 2**：pip 直连 PyPI 慢，可加 `-i http://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn`。

## 4. 配置管理员（`backend/.env`）

```powershell
copy .env.example backend\.env
# 编辑 backend\.env：
#   ZC_ADMIN_USERS=你的用户名      ← 这个账号登录后就是管理员（逗号分隔可写多个）
#   ZC_ADMIN_KEY=（留空则首次启动自动生成）兜底密钥，脚本/首次引导用
```

两种进后台的方式：

| 方式 | 怎么用 | 什么时候用 |
|---|---|---|
| **管理员账号**（日常） | 先在用户端注册（或在后台建），把用户名填进 `ZC_ADMIN_USERS`，重启后端 → 用该账号在 `/admin/` 登录 | 平时都用这个 |
| **兜底密钥** | 启动时控制台会打印 `ZC_ADMIN_KEY`，在 `/admin/` 点「用兜底密钥登录」 | 脚本调用、或还没有管理员账号时进去建账号 |

名字还没注册也没关系：注册该用户名后**自动成为管理员**（不用改配置、不用重启）。

`backend/.env` 可配项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `ZC_ADMIN_USERS` | 空 | 管理员用户名名单（逗号分隔，支持中文名） |
| `ZC_ADMIN_KEY` | 首次启动随机生成 | 兜底密钥 |
| `ZC_PORT` | `18100` | 服务端口（用户端 + 接口 + 后台同端口） |
| `ZC_HOST` | `0.0.0.0` | 监听地址（`0.0.0.0` 才能被手机访问） |
| `ZC_ALLOW_REGISTER` | `1` | 是否允许用户自助注册（后台也可随时开关） |
| `ZC_TOKEN_TTL_DAYS` | `60` | 登录态有效期（天） |
| `ZC_DATA_DIR` / `ZC_RENDER_ZOOM` / `ZC_QNO_MAX` … | 见 `backend/config.py` | 数据目录、渲染 DPI、题号范围等 |

## 5. 启动

```powershell
# 后端 + 用户端 H5 + 管理后台（同端口 18100）
.\.venv\Scripts\python.exe -m backend
# 或双击 start.bat；也可用 uvicorn backend.main:app --port 18100 --reload

# 管理后台开发态（可选，改代码热更新；接口代理到 18100）
cd admin; npm install --registry=https://registry.npmmirror.com; npm run dev   # 5173
```

- 用户端：<http://127.0.0.1:18100/> （手机同局域网访问 `http://<电脑IP>:18100/`）
- 管理后台：<http://127.0.0.1:18100/admin/>（首次进要填管理员密钥）
- 接口文档：<http://127.0.0.1:18100/docs>

## 6. 使用流程

### 管理员（`/admin/`）
1. **用管理员账号登录**（用户名在 `ZC_ADMIN_USERS` 里）；进不去时可以点「用兜底密钥登录」。
2. 左侧可开关「允许用户自助注册」，也能直接给用户开账号、勾选谁当管理员、删用户。
3. 填年份 + 选 PDF → **上传并自动切题**（进度条实时走 渲染 → OCR → 切题）。
4. 页面图上拖色块上下边缘改边界（可开「相邻题边界联动」），点色块选位置后「✂ 在此处切分」；
   右侧列表可改题号/科目/题型/分值、并入上一题、删除。
5. 改完点「整卷重裁」重新出图。
6. 顶部切到「**试卷结构**」：切完题会自动按题目分好组（题号范围 + 每题分值，综合题的分值还会尝试
   从卷面印的 `(8分)` 里读出来），在这里能改范围/分值/科目、加组加题，底部实时显示各模块与总满分
   （不是 150 会提示，但不强制）。**改完点「保存结构」**，用户端录成绩的表单和算分立刻按新结构走；
   人工校正过题号/科目/分值后点「按题目重建」重新推导。已经存过的成绩不会被重新解释。

### 用户端 H5（`/`）
1. **先登录/注册**（不登录看不到任何题目）——管理后台和用户端共用同一个登录态，在同一个浏览器里登录一次即可。
2. **选错题**：按年份/科目筛选 → 点卡片多选 → 底部「生成 Word 错题本」→ 自动下载 `408错题本_YYYY-MM-DD_HHMM.docx`。
   （勾选状态存浏览器本地，不占服务器）
3. **得分**：
   - 点「录入成绩」：选真题年份 + 做题日期 → 表单**按那份试卷的结构生成**（选择题逐组填**答对个数**，
     组数和题号范围取自试卷，某年 DS 只有 10 题就只显示 10 题）→ 综合题逐题填**得分/满分**
     （满分默认取卷面结构，可手改）→ 页面实时算模块分与总分 → 保存。
     该年份还没导入真题时退回默认结构（1-11/12-22/23-32/33-40，每题 2 分），表单上会写清楚。
   - **客观题与主观题分开算，再汇总**：表单下方实时给三行 —— 「客观题 x / y（%）」「主观题 a / b（%）」
     「总分 n / m（%）」，模块分也是客观 + 主观之和。记录列表每条同样显示这三块，
     曲线图上方的当前水平估计再给最近几次的客观/主观得分率，一眼能看出是客观题还是主观题拖了后腿。
   - 趋势图：X 轴可切「按做题时间 / 按真题年份」，同年多条可选「取最近一次/平均/最高」；
     模块得分率走左轴（0~100%），总分走右轴（0~150）；点/摸图上的点看当次明细，图例可点掉某条线。
   - 曲线图上方的**当前水平估计**（一个小表格，不是图）：最近三次成绩加权 —— 最近 50%、第二近 35%、第三近 15%；
     不足三次按比例归一化（1 次就是 100%，2 次是 50/85≈58.8% 与 35/85≈41.2%）。
     其中**总分用原始分加权**，**模块先按各条记录当时的满分算得分率再加权**（各年分布不同，
     不按固定 45 分除，避免偏差）；总分的满分取各次记录满分的加权平均（都是 150 分的卷子就是 150）。
     它始终按做题时间取最近三次，与图表 X 轴怎么切无关。
4. **导出记录**：历史生成过的 Word，可重新下载或删除。

### 安卓端
见第 9 节。

## 7. 鉴权模型（三档）

| 档位 | 范围 | 凭证 |
|---|---|---|
| 公开 | `/api/health`、`/api/meta`、`/api/auth/status`、登录/注册 | 无 |
| **登录即可** | 题目列表/详情/题目图（GET）、`/api/catalog`、`POST /api/export`、`/api/exports*`、`/api/scores*`、`/api/auth/me|logout` | 会话 Cookie **或** `Authorization: Bearer <token>` |
| 管理员 | 其余全部 `/api/*`：上传真题、切题校正（改/删题目）、重裁、删除真题、用户管理 | 管理员账号的登录态（`is_admin`），或 `X-Admin-Key` 兜底 |

几个设计点：

- **为什么同时给 Cookie 和 token**：浏览器里 `<img src="/api/questions/1/images/0">` 和 Word 下载链接
  **没法带自定义请求头**，只能靠 Cookie 自动携带；token 留给安卓/脚本/跨端调用。
- **改题属管理操作**：`GET /api/questions*` 登录即可，但 `PATCH/DELETE /api/questions/{id}` 要管理员
  （权限判断按「方法 + 路径」，不是光看前缀）。
- **默认拒绝**：没在白名单里的 `/api/*` 一律要管理员，以后新增接口忘了标注也不会漏。
- **卷面结构要管理员**：`GET/PUT /api/papers/{id}/structure` 与 `/structure/rebuild` 都在管理档；
  用户端只通过 `GET /api/scores/schema?year=`（登录即可）拿到**自己要用**的表单结构。
- 已登录但不是管理员去访问管理接口 → **403**（提示怎么把自己加进 `ZC_ADMIN_USERS`），前端据此提示换账号。

## 8. Word 排版规则（沿用 AGENTS.md）

- A4、边距 2cm，图片宽 16cm 等比缩放；
- 同一道题尽量不跨页：放不下就换页（`w:pageBreakBefore`，不是插分页符空段）；
- 整题超过一页才按小问拆，每个小问同样「放不下就换页」；
- 每张图带 `w:keepLines`，同题多图之间带 `w:keepNext`（跨页题连续插入，中间不分页）；
- 输出文件名 `408错题本_YYYY-MM-DD_HHMM.docx`。

## 9. 安卓端

现成安装包：**`android/dist/408错题本-1.1.apk`**（约 31KB，minSdk 24 / targetSdk 34，
debug 签名）。传到手机点击安装（需允许「安装未知来源应用」）。

- 它是个 WebView 壳，界面就是 `web/` 那份 H5，所以登录、得分趋势、错题勾选都能用；
- 首次打开要填**服务器地址**（如 `http://192.168.1.5:18100`），手机需与电脑同一局域网；顶部「服务器」按钮随时改；
- 顶部「刷新」会**先清掉 WebView 缓存再加载**：前端是无构建的，改完 `web/` 点它就一定拿到新页面
  （后端也给静态资源加了 `no-cache` + 版本戳，双保险）；
- 登录态用 Cookie 存在 WebView 里（已显式打开 `CookieManager` 并落盘），下次打开不用重新登录；
- 导出 Word 走系统下载器，存到「下载」目录，通知栏点开即可。

改完代码想重新打包：

```powershell
.\.venv\Scripts\python.exe android\fetch_sdk.py    # 首次：下载 platform + build-tools + R8（约 130MB，走腾讯镜像）
.\.venv\Scripts\python.exe android\build_apk.py    # 十几秒出包 -> android/dist/
```

> 不用 Gradle / Android Studio：只有一个 Activity、零 androidx 依赖，
> 直接 aapt2 → javac → d8 → zipalign → apksigner 手工流水线，比拉几百 MB Maven 依赖快得多。
> **坑**：build-tools 34 自带的 d8 是 R8 8.2.2，遇到 JDK 21 编译出的匿名内部类会崩，
> 所以 `fetch_sdk.py` 会额外下一个新版 `r8.jar`。

## 10. 测试

```powershell
.\.venv\Scripts\python.exe tests\test_ocr_crop.py    # 题号正则/过滤/裁剪，秒级
.\.venv\Scripts\python.exe tests\test_layout.py      # 换页与拆分规则，秒级
.\.venv\Scripts\python.exe tests\test_scores.py      # 算分/趋势/当前水平估计/密码与 token，秒级
.\.venv\Scripts\python.exe tests\test_structure.py   # 卷面结构：自动推导/校验/按结构算分/读卷面分值，秒级
.\.venv\Scripts\python.exe tests\test_auth_admin.py  # 鉴权：公开/登录/管理员 三档与权限边界，秒级
node tests\web_dom_smoke.js                          # 前端交互（登录门/Tab/录入表单），迷你 DOM 跑真脚本，秒级
.\.venv\Scripts\python.exe tests\e2e.py              # 端到端（含 OCR，约 1 分钟）
.\.venv\Scripts\python.exe tests\http_smoke.py       # HTTP 全链路（需先启动服务）
```

`e2e.py` 会自造两页图片型 PDF（含跨页题、10 个小问、页脚），在 `data/_selftest/` 下跑完整流程，
并输出 `crops_montage.png` 拼接图供肉眼检查切题结果。`http_smoke.py` 覆盖鉴权（未登录/非管理员/密钥三档）、
Cookie 登录态、用户注册登录、得分录入/编辑/删除/趋势/水平估计、**卷面结构（读/改/重建 + 用户端表单跟着变）**、
错题校正、导出下载校验，跑完自动清理测试数据。`container_check.py` 打一遍容器/线上实例的首页、后台与接口：

```powershell
.\.venv\Scripts\python.exe tests\container_check.py http://127.0.0.1:18101   # 参数是目标地址
```

## 11. 待办

- 题目文本 OCR 落库，支持按关键词搜索；
- 得分趋势增加「历史最高/最低/平均」卡片、按模块切换单线；
- 导出 PDF、Anki 卡片；错题勾选记录云端同步（当前刻意只存本地）；
- 录入时把「试卷结构」直接同步给用户端做只读提示（现在只在表单上显示来源）。

## 12. Docker 部署（丢服务器上）

一个容器搞定：FastAPI 同时托管用户端 H5（`/`）、管理后台（`/admin/`）、接口（`/api/*`），
数据全在 `./data`（SQLite + 上传的 PDF + 页面图 + 裁剪图 + 导出的 Word）。

```bash
# 1) 服务器上装好 docker + compose，把仓库拉下来（或整个目录 rsync 上去）
git clone <你的仓库> 408-wrong-cropper && cd 408-wrong-cropper

# 2) 改 docker-compose.yml 里的 ZC_ADMIN_USERS（你的用户名）；想固定兜底密钥就填 ZC_ADMIN_KEY
vi docker-compose.yml

# 3) 构建 + 后台启动（首次构建约 3~5 分钟，镜像约 1.3GB：onnxruntime + opencv + pymupdf）
docker compose up -d --build

# 4) 看启动横幅（会打印访问地址、管理员账号、兜底密钥）
docker compose logs -f app
```

> 构建默认走国内源（apt→清华、pip→清华、npm→npmmirror），在国内服务器上快很多；
> 要用官方源：`docker build --build-arg APT_MIRROR=deb.debian.org --build-arg PIP_INDEX=https://pypi.org/simple -t 408-wrong-cropper .`。
> 首次构建慢在 apt（约 40 秒）和 pip 装 onnxruntime（1~3 分钟），**这两层之后都会被缓存**，
> 改代码后重建（`docker compose up -d --build`）只要十几秒。

打开 `http://服务器IP:18100/` 就是用户端，`/admin/` 是管理后台。第一次进管理后台用密钥登录，
去用户端注册一个和 `ZC_ADMIN_USERS` 同名的账号（注册后自动成为管理员），以后就用账号登录。

**想把自己本机的成绩带过去**：把本机 `data/db.sqlite` 拷到服务器的 `./data/`（停服再拷更稳妥）：

```bash
docker compose down
scp data/db.sqlite server:/path/to/408-wrong-cropper/data/
docker compose up -d
```

**常用操作**：

```bash
docker compose ps                 # 状态（healthy 才算真起来）
docker compose restart            # 重启
docker compose down               # 停止（数据在 ./data，不会丢）
docker compose up -d --build      # 改了代码后重新构建
docker compose logs -f --tail=50  # 看日志
```

**几个要点**：

- **端口**：容器里固定 18100，宿主机端口在 compose 的 `ports` 里改（例如 `"8080:18100"`）；
- **数据卷**：`./data:/data`。Linux 上如果容器报权限错误，执行一次 `sudo chown -R 10001:10001 data`
  （容器里用 uid 10001 的非 root 用户跑）；Windows/macOS 的 Docker Desktop 不用管；
- **环境变量**：`ZC_ADMIN_USERS`（管理员用户名）、`ZC_ADMIN_KEY`（兜底密钥，留空则每次启动随机生成、
  只在日志里能看到，**想固定脚本调用就填上**）、`ZC_ALLOW_REGISTER`、`TZ`、`ZC_DATA_DIR=/data`；
- **健康检查**：镜像自带 `HEALTHCHECK`（打 `/api/health`），`docker compose ps` 显示 healthy 即可；
- **反向代理**：想上 https 就在前面挂 Caddy/Nginx 反代到 `127.0.0.1:18100`；
  应用只用同源请求，反代不用额外配置；
- **资源**：OCR（onnxruntime）跑起来占 500MB~1G 内存，建议服务器 ≥ 2G；
- **不进镜像的东西**：`backend/.env`（管理密钥）、`data/`、`android/`、`tests/` 都被 `.dockerignore` 排除，
  密钥只走环境变量。
