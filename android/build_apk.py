"""手工把 android/app 打成 APK —— 不需要 Gradle / Android Studio。

流水线（每一步都是 SDK 自带命令行工具）：
    PIL 生成图标 -> aapt2 compile -> aapt2 link -> javac -> jar -> d8 -> zip(加 classes.dex)
    -> zipalign -> apksigner（debug 签名）

为什么不用 Gradle/AGP：本项目只有一个 Activity、零 androidx 依赖，
Gradle 反而要再拉几百 MB Maven 依赖；手工流水线十几秒就能出包。

前置：先跑 android/fetch_sdk.py 下载 platform + build-tools。
跑法：.venv\\Scripts\\python.exe android\\build_apk.py
产物：android/dist/408错题本-1.3.apk
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ANDROID_DIR = Path(__file__).resolve().parent
ROOT = ANDROID_DIR.parent
SDK = ROOT / ".android-sdk"
BUILD = ANDROID_DIR / "build"
DIST = ANDROID_DIR / "dist"

APP = ANDROID_DIR / "app"
MANIFEST = APP / "AndroidManifest.xml"
RES = APP / "res"
JAVA_SRC = APP / "java"
PACKAGE_PATH = Path("com") / "zc" / "wrongbook"
MAIN_ACTIVITY = JAVA_SRC / PACKAGE_PATH / "MainActivity.java"

ANDROID_JAR = SDK / "platforms" / "android-34" / "android.jar"
BUILD_TOOLS = SDK / "build-tools" / "34.0.0"

MIN_SDK = "24"
TARGET_SDK = "34"
VERSION_CODE = "4"
VERSION_NAME = "1.3"
APK_NAME = f"408错题本-{VERSION_NAME}.apk"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


def tool(name: str) -> Path:
    path = BUILD_TOOLS / name
    if not path.exists():
        raise SystemExit(f"缺少 {path}，请先运行：.venv\\Scripts\\python.exe android\\fetch_sdk.py")
    return path


def java_home_bin(name: str) -> str:
    exe = shutil.which(name)
    if exe:
        return exe
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = Path(home) / "bin" / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"找不到 {name}，请装 JDK 并设置 JAVA_HOME（当前 JAVA_HOME={os.environ.get('JAVA_HOME')}）")


def run(cmd: list[str], label: str) -> None:
    print(f"  $ {label}")
    result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        print(out.strip()[-3000:])
        raise SystemExit(f"{label} 失败（exit {result.returncode}）")
    tail = [line for line in out.strip().splitlines() if line.strip()][-3:]
    for line in tail:
        print(f"    {line}")


# ---------------------------------------------------------------- 图标
def make_icons() -> None:
    """用 Pillow 生成各密度启动图标（省得往仓库塞二进制）。"""
    targets = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    missing = [d for d in targets if not (RES / f"mipmap-{d}" / "ic_launcher.png").exists()]
    if not missing:
        return
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((p for p in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\msyhbd.ttc") if Path(p).exists()), None)
    for density in missing:
        size = targets[density]
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=(47, 111, 237, 255))
        # 一条白色"书页"横线 + 408 字样
        draw.rounded_rectangle(
            [size * 0.16, size * 0.70, size * 0.84, size * 0.76],
            radius=int(size * 0.03),
            fill=(255, 255, 255, 235),
        )
        if font_path:
            font = ImageFont.truetype(font_path, int(size * 0.40))
            text = "408"
            box = draw.textbbox((0, 0), text, font=font)
            draw.text(
                ((size - (box[2] - box[0])) / 2 - box[0], size * 0.20 - box[1]),
                text,
                font=font,
                fill=(255, 255, 255, 255),
            )
        out = RES / f"mipmap-{density}"
        out.mkdir(parents=True, exist_ok=True)
        img.save(out / "ic_launcher.png")
    print(f"  图标已生成：mipmap-{{{','.join(missing)}}}")


# ---------------------------------------------------------------- 打包
def package_apk(base_apk: Path, dex_dir: Path, out_apk: Path) -> None:
    """把 classes.dex 塞进 aapt2 产出的资源包。

    resources.arsc 必须保持**不压缩**：targetSdk 30+ 在 Android 11 上会校验，
    压缩过的会装不上（INSTALL_PARSE_FAILED_UNEXPECTED_EXCEPTION）。
    """
    dex_files = sorted(dex_dir.glob("*.dex"))
    if not dex_files:
        raise SystemExit("d8 没有产出 classes.dex")
    with zipfile.ZipFile(base_apk) as src, zipfile.ZipFile(out_apk, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "resources.arsc":
                dst.writestr(zipfile.ZipInfo("resources.arsc"), data, zipfile.ZIP_STORED)
            else:
                dst.writestr(info.filename, data)
        for dex in dex_files:
            dst.write(dex, dex.name)


def main() -> int:
    if not ANDROID_JAR.exists():
        raise SystemExit("没找到 android.jar，请先运行：.venv\\Scripts\\python.exe android\\fetch_sdk.py")
    if not MAIN_ACTIVITY.exists():
        raise SystemExit(f"缺少源码 {MAIN_ACTIVITY}")

    build_tools = tool("aapt2.exe")
    zipalign = tool("zipalign.exe")
    # 优先用单独的 r8.jar（新版）：build-tools 34 自带的 d8 是 R8 8.2.2，
    # 遇到 JDK 21 编译出来的匿名内部类会 NPE。见 fetch_sdk.py 里的说明。
    r8_jar = SDK / "r8" / "r8.jar"
    d8_classpath = r8_jar if r8_jar.exists() else BUILD_TOOLS / "lib" / "d8.jar"
    debug_info = r8_jar.exists()  # 旧 d8 只能靠 -g:none 绕过
    apksigner_jar = BUILD_TOOLS / "lib" / "apksigner.jar"
    javac = java_home_bin("javac")
    jar = java_home_bin("jar")
    keytool = java_home_bin("keytool")
    java = java_home_bin("java")

    for folder in (BUILD / "gen", BUILD / "classes", BUILD / "dex", DIST):
        shutil.rmtree(folder, ignore_errors=True)   # 清掉上次的产物，避免残留 class 混进来
        folder.mkdir(parents=True, exist_ok=True)

    print("[1/7] 生成图标")
    make_icons()

    print("[2/7] aapt2 compile（编译资源）")
    res_zip = BUILD / "res.zip"
    run([build_tools, "compile", "--dir", RES, "-o", res_zip], "aapt2 compile")

    print("[3/7] aapt2 link（生成资源 APK + R.java）")
    base_apk = BUILD / "base.apk"
    run(
        [
            build_tools, "link",
            "-o", base_apk,
            "-I", ANDROID_JAR,
            "--manifest", MANIFEST,
            "--java", BUILD / "gen",
            "--min-sdk-version", MIN_SDK,
            "--target-sdk-version", TARGET_SDK,
            "--version-code", VERSION_CODE,
            "--version-name", VERSION_NAME,
            "--auto-add-overlay",
            res_zip,
        ],
        "aapt2 link",
    )

    print("[4/7] javac（编译 Java）" + ("" if debug_info else "（旧 d8：关掉调试信息以绕开 R8 8.2 的 bug）"))
    r_java = BUILD / "gen" / PACKAGE_PATH / "R.java"
    sources = [MAIN_ACTIVITY, r_java]
    debug_flags = [] if debug_info else ["-g:none"]
    try:
        run(
            [javac, "-encoding", "UTF-8", "--release", "8", *debug_flags, "-cp", ANDROID_JAR,
             "-d", BUILD / "classes", *sources],
            "javac --release 8",
        )
    except SystemExit:
        print("    --release 8 不可用，回退到 -source/-target 8 + bootclasspath")
        run(
            [javac, "-encoding", "UTF-8", "-source", "8", "-target", "8", *debug_flags,
             "-bootclasspath", ANDROID_JAR, "-cp", ANDROID_JAR,
             "-d", BUILD / "classes", *sources],
            "javac -source 8",
        )

    classes_jar = BUILD / "classes.jar"
    run([jar, "--create", "--file", classes_jar, "-C", BUILD / "classes", "."], "jar 打包 class")

    print("[5/7] d8（转 dex）")
    run(
        [java, "-cp", d8_classpath, "com.android.tools.r8.D8", "--release",
         "--min-api", MIN_SDK, "--lib", ANDROID_JAR,
         "--output", BUILD / "dex", classes_jar],
        f"d8 ({Path(d8_classpath).name})",
    )

    print("[6/7] 打包 + zipalign")
    unsigned = BUILD / "unsigned.apk"
    package_apk(base_apk, BUILD / "dex", unsigned)
    aligned = BUILD / "aligned.apk"
    run([zipalign, "-p", "-f", "4", unsigned, aligned], "zipalign")

    print("[7/7] 签名（debug 证书）")
    keystore = BUILD / "debug.keystore"
    if not keystore.exists():
        run(
            [keytool, "-genkeypair", "-keystore", keystore, "-alias", "androiddebugkey",
             "-storepass", "android", "-keypass", "android", "-keyalg", "RSA", "-keysize", "2048",
             "-validity", "10000", "-dname", "CN=Android Debug,O=Android,C=CN"],
            "keytool 生成调试证书",
        )
    apk = DIST / APK_NAME
    run(
        [java, "-jar", apksigner_jar, "sign", "--ks", keystore,
         "--ks-pass", "pass:android", "--key-pass", "pass:android",
         "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
         "--v4-signing-enabled", "false",  # 不生成 .idsig（对直接安装没用，只多一个文件）
         "--out", apk, aligned],
        "apksigner sign",
    )
    run([java, "-jar", apksigner_jar, "verify", "--print-certs", apk], "apksigner verify")
    run([build_tools, "dump", "badging", apk], "aapt2 dump badging")

    size_mb = apk.stat().st_size / 1048576
    print(f"\nAPK 就绪：{apk}  ({size_mb:.2f} MB)")
    print("传到手机点击安装即可（需要允许「安装未知来源应用」）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
