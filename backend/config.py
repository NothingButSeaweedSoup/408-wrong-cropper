"""全局配置。

所有可调参数集中在此，可用环境变量（前缀 ZC_）覆盖，避免散落在各 service 里。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- .env
# 极简 .env 解析（不引 python-dotenv）：KEY=VALUE，支持 # 注释、引号。
# 优先级：系统环境变量 > backend/.env > 根目录 .env
_REAL_ENV = set(os.environ)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in _REAL_ENV:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


load_env_file(ROOT_DIR / ".env")
load_env_file(ROOT_DIR / "backend" / ".env")

# ---------------------------------------------------------------- 目录
DATA_DIR = Path(os.getenv("ZC_DATA_DIR") or ROOT_DIR / "data")
UPLOAD_DIR = DATA_DIR / "uploads"
PAGES_DIR = DATA_DIR / "pages"
CROPS_DIR = DATA_DIR / "crops"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = Path(os.getenv("ZC_DB_PATH") or DATA_DIR / "db.sqlite")
WEB_DIR = ROOT_DIR / "web"  # 用户端 H5（静态，无构建）
ADMIN_DIST_DIR = ROOT_DIR / "admin" / "dist"  # Vue 管理后台构建产物（可选挂载）
ENV_FILE = ROOT_DIR / "backend" / ".env"  # 管理密钥缺省写这里

# ---------------------------------------------------------------- 服务
# 服务端端口（用户端 H5 / 接口 / 管理后台同端口）
SERVER_HOST = os.getenv("ZC_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("ZC_PORT", "18100"))

# ---------------------------------------------------------------- 鉴权
# 管理后台默认"用普通用户账号登录"：用户名出现在 ZC_ADMIN_USERS 里的用户就是管理员。
# ZC_ADMIN_KEY 保留作兜底（脚本/curl/首次引导），不是日常登录方式。
ADMIN_KEY_HEADER = "X-Admin-Key"
ADMIN_KEY = os.getenv("ZC_ADMIN_KEY") or os.getenv("ADMIN_KEY") or ""
ADMIN_USERS: frozenset[str] = frozenset(
    name.strip()
    for name in (os.getenv("ZC_ADMIN_USERS") or os.getenv("ZC_ADMIN_USER") or "")
    .replace("，", ",")
    .split(",")
    if name.strip()
)


def is_admin_username(username: str | None) -> bool:
    """用户名是否在 .env 的管理员名单里（名单里的账号登录后即管理员）。"""
    return bool(username) and username.strip() in ADMIN_USERS


def ensure_admin_key() -> str:
    """没配置兜底密钥就随机生成一个并写进 backend/.env。

    日常登录用账号（见 ZC_ADMIN_USERS），这个密钥只是兜底：脚本调用、
    以及"还没有管理员账号时"进后台把账号建起来。返回生效的密钥。
    """
    global ADMIN_KEY
    if ADMIN_KEY:
        return ADMIN_KEY
    ADMIN_KEY = secrets.token_urlsafe(24)
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not ENV_FILE.exists() or ENV_FILE.stat().st_size == 0 else "\n"
    # 用 utf-8-sig：新建文件时带 BOM，Windows PowerShell 5.1 读中文注释才不会乱码
    with ENV_FILE.open("a", encoding="utf-8-sig") as fp:
        fp.write(f"{prefix}# 管理后台密钥（首次启动自动生成，改完要重启后端）\nZC_ADMIN_KEY={ADMIN_KEY}\n")
    return ADMIN_KEY


def ensure_dirs() -> None:
    """首次启动时创建数据目录。"""
    for d in (DATA_DIR, UPLOAD_DIR, PAGES_DIR, CROPS_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 渲染
# zoom=3.0 对应 72*3 = 216 DPI，与 AGENTS.md 5.1 一致。
RENDER_ZOOM = float(os.getenv("ZC_RENDER_ZOOM", "3.0"))
# 页面图存成灰度可把体积压到 1/3，扫描件本来就是黑白的；彩色真题可置 0。
RENDER_GRAYSCALE = os.getenv("ZC_RENDER_GRAYSCALE", "1") not in ("0", "", "false", "False")

# ---------------------------------------------------------------- 切题
PAD_TOP_PX = int(os.getenv("ZC_PAD_TOP_PX", "15"))  # 题号上方留白
PAD_BOTTOM_PX = int(os.getenv("ZC_PAD_BOTTOM_PX", "15"))  # 题号上方留白（下一题的上边界）
FOOTER_TRIM_RATIO = float(os.getenv("ZC_FOOTER_TRIM_RATIO", "0.03"))  # 页脚裁掉比例
HEADER_TRIM_RATIO = float(os.getenv("ZC_HEADER_TRIM_RATIO", "0.02"))  # 页眉裁掉比例
# 题号必须落在页面左侧这个比例内，用于排除正文里的数字（AGENTS.md 5.2 的 x0 < 250 等价物）。
# 按比例比写死 250px 更稳：换渲染 DPI 时不用改配置。
NUMBER_X_RATIO = float(os.getenv("ZC_NUMBER_X_RATIO", "0.25"))
# 408 题号范围：1~40 选择 + 41~47 综合。AGENTS.md 写的是 1~45，实际真题到 47 题。
QNO_MIN = int(os.getenv("ZC_QNO_MIN", "1"))
QNO_MAX = int(os.getenv("ZC_QNO_MAX", "47"))
# 跨页判定：下一页题号上方墨迹占比超过该阈值即认为"有内容"，属于上一题的续页。
INK_ROW_RATIO = float(os.getenv("ZC_INK_ROW_RATIO", "0.012"))
INK_MIN_ROWS = int(os.getenv("ZC_INK_MIN_ROWS", "12"))

# ---------------------------------------------------------------- OCR
OCR_MIN_CONFIDENCE = float(os.getenv("ZC_OCR_MIN_CONFIDENCE", "0.6"))
OCR_TEXT_SCORE_MIN = float(os.getenv("ZC_OCR_TEXT_SCORE_MIN", "0.5"))

# ---------------------------------------------------------------- Word 排版
PAGE_W_CM = 21.0  # A4
PAGE_H_CM = 29.7
MARGIN_CM = 2.0
IMAGE_WIDTH_CM = float(os.getenv("ZC_IMAGE_WIDTH_CM", "16.0"))
CONTENT_W_CM = PAGE_W_CM - 2 * MARGIN_CM
CONTENT_H_CM = PAGE_H_CM - 2 * MARGIN_CM
# 段落级固定开销（磅），用于换页估算
SPACING_PT_PER_GAP = 6.0
CAPTION_HEIGHT_CM = 0.75
NOTE_LINE_CM = 0.85
# Word 分页不是像素级精确，估算时留一点余量，宁松勿紧（AGENTS.md 11 节）
LAYOUT_SAFETY_RATIO = float(os.getenv("ZC_LAYOUT_SAFETY_RATIO", "0.96"))

# ---------------------------------------------------------------- 408 试卷结构
# (题号下限, 题号上限, 科目, 题型, 每题分值)
# 408 标准分布：选择 40 题 x 2 分 = 80 分，综合 41~47 = 70 分；
# 合计 DS 45 / 计组 45 / OS 35 / 计网 25 = 150 分。
SUBJECT_RULES: tuple[tuple[int, int, str, str, float | None], ...] = (
    (1, 11, "ds", "choice", 2.0),
    (12, 22, "co", "choice", 2.0),
    (23, 32, "os", "choice", 2.0),
    (33, 40, "cn", "choice", 2.0),
    (41, 42, "ds", "subjective", None),
    (43, 44, "co", "subjective", None),
    (45, 46, "os", "subjective", None),
    (47, 47, "cn", "subjective", None),
)

SUBJECT_NAMES = {"ds": "数据结构", "co": "计算机组成原理", "os": "操作系统", "cn": "计算机网络"}
MODULE_FULL_SCORE = {"total": 150.0, "ds": 45.0, "co": 45.0, "os": 35.0, "cn": 25.0}

PAPER_STATUS = ("uploaded", "rendered", "split", "corrected")

# ---------------------------------------------------------------- 得分录入
# 选择题分组：(科目, 题号下限, 题号上限, 每题分值)
CHOICE_GROUPS: tuple[tuple[str, int, int, float], ...] = (
    ("ds", 1, 11, 2.0),
    ("co", 12, 22, 2.0),
    ("os", 23, 32, 2.0),
    ("cn", 33, 40, 2.0),
)
# 综合题 41~47 的默认满分。各年份分布不完全一样，所以录入表单里允许逐题改。
SUBJECTIVE_FULL_DEFAULT: dict[int, float] = {41: 10.0, 42: 13.0, 43: 13.0, 44: 10.0, 45: 7.0, 46: 8.0, 47: 9.0}

# ---------------------------------------------------------------- 用户系统
ALLOW_REGISTER_DEFAULT = os.getenv("ZC_ALLOW_REGISTER", "1") not in ("0", "", "false", "False")
TOKEN_TTL_DAYS = int(os.getenv("ZC_TOKEN_TTL_DAYS", "60"))
MIN_PASSWORD_LEN = 6
# 登录态同时下发 Cookie：浏览器里 <img src> 和文件下载链接没法带 Authorization 头，
# 只能靠 Cookie 自动携带（安卓 WebView 同样自动带）。
TOKEN_COOKIE = "zc_token"


def subject_of(qno: int) -> tuple[str, str, float | None]:
    """按题号推断 (科目, 题型, 分值)。识别不准时由人工校正接口覆盖。"""
    for lo, hi, subject, qtype, score in SUBJECT_RULES:
        if lo <= qno <= hi:
            return subject, qtype, score
    return ("ds", "choice" if qno <= 40 else "subjective", None)


def score_form_schema(year: int | None = None) -> dict:
    """得分录入表单的结构（前端不写死 408 卷面分布，全部由后端给）。"""
    choice = [
        {
            "subject": subject,
            "name": SUBJECT_NAMES[subject],
            "from": lo,
            "to": hi,
            "count": hi - lo + 1,
            "per_score": per,
            "full": round((hi - lo + 1) * per, 1),
        }
        for subject, lo, hi, per in CHOICE_GROUPS
    ]
    subjective = [
        {
            "qno": qno,
            "subject": subject_of(qno)[0],
            "name": SUBJECT_NAMES[subject_of(qno)[0]],
            "full": SUBJECTIVE_FULL_DEFAULT.get(qno, 0.0),
        }
        for qno in range(41, 48)
    ]
    return {
        "year": year,
        "choice_groups": choice,
        "subjective": subjective,
        "module_full": MODULE_FULL_SCORE,
        "choice_full": round(sum(g["full"] for g in choice), 1),
        "subjective_full": round(sum(s["full"] for s in subjective), 1),
    }
