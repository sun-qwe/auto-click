"""在真实登录态下验证采集逻辑：直接对已登录的 BOSS 页面抽取职位。

这是最有说服力的验证 —— 真实页面、真实数据、真实 DOM。
只读，不投递。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让脚本可以直接 `python tools/xxx.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "cdp"))

import json
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from cdp import WS, evaluate, setup

# 与 browser.py 的 SELECTORS 保持一致，用真实类名
EXTRACT = """
(() => {
  const cards = [...document.querySelectorAll("li.job-card-box")];
  if (!cards.length) {
    // 回退到结构锚点
    return JSON.stringify({fallback: true, count: 0});
  }
  return JSON.stringify({
    fallback: false,
    count: cards.length,
    jobs: cards.slice(0, 8).map(card => {
      const pick = s => { const e = card.querySelector(s);
                          return e ? e.innerText.trim() : ''; };
      const a = card.querySelector("a[href*='/job_detail/']");
      return {
        href: a ? a.getAttribute('href') : '',
        title: pick('a.job-name'),
        salary: pick('span.job-salary'),
        company: pick('span.boss-name'),
        area: pick('span.company-location'),
        tags: [...card.querySelectorAll('ul.tag-list li')]
                .map(t => t.innerText.trim()).slice(0,5)
      };
    })
  });
})()
"""


def find_boss_tab() -> dict:
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=10).read())
    for t in tabs:
        if t.get("type") == "page" and "zhipin" in t.get("url", ""):
            return t
    raise RuntimeError("没找到 BOSS 标签页，请确认调试 Chrome 里开着 BOSS")


def main() -> int:
    tab = find_boss_tab()
    ws = WS(tab["webSocketDebuggerUrl"])
    try:
        setup(ws)

        guard = evaluate(ws, """
          JSON.stringify({
            logged: !!document.querySelector('.nav-figure img'),
            loginBtn: !!document.querySelector('.login-btn,.header-login-btn'),
            captcha: !!document.querySelector('.nc_wrapper,#nc_1_wrapper,.yidun_bgimg'),
            cards: document.querySelectorAll('li.job-card-box').length,
            jobLinks: document.querySelectorAll("a[href*='/job_detail/']").length
          })
        """)
        print(f"页面状态: {guard}")
        if "captcha\":true" in str(guard):
            print("\n[!] 遇到验证码，不要继续操作")
            return 4

        data = json.loads(evaluate(ws, EXTRACT) or "{}")
        if data.get("fallback"):
            print("[warn] li.job-card-box 没命中，需要回退到结构锚点")
            return 3

        print(f"\n成功采集 {data['count']} 个职位卡片：\n")
        for j in data["jobs"]:
            tags = ", ".join(j["tags"][:3])
            print(f"  {j['title'][:14]:16} {j['salary']:9} {j['company'][:12]:14} "
                  f"{j['area'][:18]:20} [{tags}]")

        # 验证 job_id 能否从 href 稳定提取
        from src.boss.collector import _extract_job_id
        sample = data["jobs"][0]["href"]
        jid = _extract_job_id(sample)
        print(f"\n职位 ID 提取: {sample} -> {jid}")
        assert jid, "job_id 提取失败"

        # 用真实数据跑一遍筛选
        from src.boss.filters import screen
        from src.boss.models import Job
        from tests.conftest import rules as mk

        parsed = [
            Job(job_id=_extract_job_id(j["href"]), title=j["title"],
                company=j["company"], salary=j["salary"],
                city=j["area"].split("·")[0] if j["area"] else "",
                tags=tuple(j["tags"]), url=j["href"])
            for j in data["jobs"]
        ]
        passed, skipped = screen(parsed, mk(
            title_any=["Java", "Python", "后端", "开发"],
            exclude_keywords=["外包", "外派", "驻场"]))
        print(f"\n规则筛选：通过 {len(passed)}，跳过 {len(skipped)}")
        for j, r in skipped[:4]:
            print(f"  SKIP {j.title[:14]:16} {r}")

        print("\n[ok] 真实登录态采集验证通过")
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    raise SystemExit(main())
