"""看一眼库里的真题（年份 + 题量），确认有没有"全都挤在同一年"的情况。"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:18100"
KEY = [
    line.split("=", 1)[1].strip()
    for line in open("backend/.env", encoding="utf-8-sig")
    if line.startswith("ZC_ADMIN_KEY=")
][0]


def call(path: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload else None
    request = urllib.request.Request(
        BASE + path, data=data, headers={"X-Admin-Key": KEY, "Content-Type": "application/json"}, method=method
    )
    return json.load(urllib.request.urlopen(request))


papers = call("/api/papers")
print(f"共 {len(papers)} 份真题：")
for paper in sorted(papers, key=lambda p: (p["year"], p["id"])):
    print(f"  id={paper['id']:>3}  {paper['year']} 年  {paper['question_count']:>3} 题  "
          f"{paper['status']:<8} {paper['title']!r}")
print("\n按年份汇总：")
counts: dict[int, int] = {}
for paper in papers:
    counts[paper["year"]] = counts.get(paper["year"], 0) + 1
for year, n in sorted(counts.items()):
    print(f"  {year}: {n} 份")
