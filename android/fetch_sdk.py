"""下载构建 APK 所需的最小 Android SDK 组件（不需要 Gradle / Android Studio）。

只要两样东西，总共约 110MB：
    platforms/android-34/android.jar        —— 编译期的 Android API
    build-tools/34.0.0/{aapt2,d8,zipalign,apksigner} —— 打包、dex、对齐、签名

刻意不用 cmdline-tools(146MB) 和 Gradle/AGP(还要再拉几百 MB Maven 依赖)：
本项目只有一个 Activity、零 androidx 依赖，手工流水线足够且快得多。

用法：.venv\\Scripts\\python.exe android\\fetch_sdk.py
产物：<项目根>/.android-sdk/（已 gitignore）
"""

from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / ".android-sdk"
DL = SDK / "_dl"
MIRROR = "https://mirrors.cloud.tencent.com/AndroidSDK/"

PACKAGES = {
    # 名称: (远程文件名, 解压目标目录)
    "platform": ("platform-34-ext7_r03.zip", SDK / "platforms" / "android-34"),
    "build-tools": ("build-tools_r34-windows.zip", SDK / "build-tools" / "34.0.0"),
}

# 单独的 R8 包：build-tools 34 自带的 d8 是 R8 8.2.2，
# 在 JDK 21 编译出的匿名内部类上会崩（NPE），换成新版就好。
# 放在 Google Maven 的 Aliyun 镜像上，比整个 build-tools 新版本小得多。
SINGLE_FILES = {
    "r8": (
        "https://maven.aliyun.com/repository/google/com/android/tools/r8/8.7.18/r8-8.7.18.jar",
        SDK / "r8" / "r8.jar",
    ),
}

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


def download(name: str, url: str, dest: Path) -> Path:
    """带断点续传的下载（网速慢时中断了可以直接重跑）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = dest.stat().st_size if dest.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0)) + have
            mode = "ab" if have and resp.status == 206 else "wb"
            if mode == "wb":
                have = 0
            print(f"  {name}: 开始下载 {total / 1048576:.1f} MB -> {dest.name}", flush=True)
            done = have
            with dest.open(mode) as fp:
                while chunk := resp.read(256 * 1024):
                    fp.write(chunk)
                    done += len(chunk)
                    if total and done % (8 * 1048576) < 256 * 1024:
                        print(f"    {done / 1048576:.1f}/{total / 1048576:.1f} MB", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: 下载中断（{exc}），重跑本脚本可续传", flush=True)
        raise
    return dest


def extract(zip_path: Path, target: Path) -> None:
    """把压缩包内容摊平到 target（包内可能自带一层目录名）。"""
    if any(target.glob("**/android.jar")) or (target / "aapt2.exe").exists():
        print(f"  已解压，跳过：{target}")
        return
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        top = {n.split("/")[0] for n in names if "/" in n}
        strip = f"{next(iter(top))}/" if len(top) == 1 else ""
        for info in zf.infolist():
            rel = info.filename[len(strip):] if strip and info.filename.startswith(strip) else info.filename
            if not rel or rel.endswith("/"):
                continue
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                dst.write(src.read())
    print(f"  解压完成：{target}")


def main() -> int:
    for name, (remote, target) in PACKAGES.items():
        print(f"[{name}] {remote}")
        zip_path = DL / remote
        need = 0 if not zip_path.exists() else zip_path.stat().st_size
        try:
            with urllib.request.urlopen(
                urllib.request.Request(MIRROR + remote, method="HEAD", headers={"User-Agent": "curl/8"}), timeout=30
            ) as resp:
                need = int(resp.headers.get("Content-Length", 0))
        except Exception:  # noqa: BLE001
            pass
        if not zip_path.exists() or (need and zip_path.stat().st_size != need):
            download(name, MIRROR + remote, zip_path)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                bad = zf.testzip()
            print(f"  压缩包校验通过（{zip_path.stat().st_size / 1048576:.1f} MB）" + (f"，损坏项 {bad}" if bad else ""))
        except zipfile.BadZipFile:
            print("  压缩包损坏，删除后重跑本脚本")
            zip_path.unlink(missing_ok=True)
            return 1
        extract(zip_path, target)

    for name, (url, target) in SINGLE_FILES.items():
        print(f"[{name}] {url.rsplit('/', 1)[-1]}")
        need = 0
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, method="HEAD", headers={"User-Agent": "curl/8"}), timeout=30
            ) as resp:
                need = int(resp.headers.get("Content-Length", 0))
        except Exception:  # noqa: BLE001
            pass
        if not target.exists() or (need and target.stat().st_size != need):
            download(name, url, target)
        print(f"  {target.stat().st_size / 1048576:.1f} MB -> {target}")

    android_jar = SDK / "platforms" / "android-34" / "android.jar"
    tools = SDK / "build-tools" / "34.0.0"
    r8_jar = SDK / "r8" / "r8.jar"
    missing = [
        str(p)
        for p in [android_jar, tools / "aapt2.exe", tools / "zipalign.exe", tools / "apksigner.bat", r8_jar]
        if not p.exists()
    ]
    if missing:
        print("缺少文件：" + ", ".join(missing))
        return 1
    print("\nSDK 就绪：")
    print(f"  android.jar  {android_jar}")
    print(f"  build-tools  {tools}")
    print(f"  r8           {r8_jar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
