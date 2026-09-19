"""打一遍容器里的前端/接口，确认镜像里那几样东西都在。"""

import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18101"
ok = True

for path, must_contain in [
    ("/", "408 错题本"),
    ("/admin/", "assets/index-"),
    ("/scores.js", "客观题"),
    ("/api/health", '"ok":true'),
    ("/api/meta", "module_full_score"),
]:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        body = resp.read().decode("utf-8", "replace")
    hit = must_contain in body
    ok = ok and hit
    print(f"  [{'PASS' if hit else 'FAIL'}] GET {path:14} {resp.status} {len(body):7}B "
          f"content-type={resp.headers.get('Content-Type', '')[:28]}")
    if not hit:
        print(f"        期望包含 {must_contain!r}")

print("全部通过" if ok else "存在失败项")
raise SystemExit(0 if ok else 1)
