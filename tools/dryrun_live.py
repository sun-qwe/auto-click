"""真实页面干跑：只采集和判定，绝不点击投递。

投一个之前先看清楚它打算投给谁 —— 这是不可撤回动作前的最后一道闸。
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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace",)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from cdp import WS, evaluate, setup
from src.boss.font_decode import SalaryGuard, is_obfuscated
from src.boss.models import Job


def boss_tab() -> dict:
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=10).read())
    for t in tabs:
        if t.get("type") == "page" and "zhipin" in t.get("url", ""):
            return t
    raise RuntimeError("没找到 BOSS 标签页")


EXTRACT = """
(() => {
  const cards = [...document.querySelectorAll('li.job-card-box')];
  return JSON.stringify(cards.map(c => {
    const pick = s => { const e = c.querySelector(s);
                        return e ? e.innerText.trim() : ''; };
    const a = c.querySelector("a[href*='/job_detail/']");
    return {
      href: a ? a.getAttribute('href') : '',
      title: pick('a.job-name'),
      salary: pick('span.job-salary'),
      company: pick('span.boss-name'),
      area: pick('span.company-location'),
      tags: [...c.querySelectorAll('ul.tag-list li')]
              .map(t => t.innerText.trim())
    };
  }));
})()
"""


def main() -> int:
    ws = WS(boss_tab()["webSocketDebuggerUrl"])
    try:
        setup(ws)

        guard_state = json.loads(evaluate(ws, """
          JSON.stringify({
            logged: !!document.querySelector('.nav-figure img'),
            loginBtn: !!document.querySelector('.login-btn,.header-login-btn'),
            captcha: !!document.querySelector('.nc_wrapper,#nc_1_wrapper,.yidun_bgimg'),
            cards: document.querySelectorAll('li.job-card-box').length
          })
        """) or "{}")
        print(f"页面状态: {guard_state}")

        if guard_state.get("captcha"):
            print("\n[!] 检测到验证码，停止。不要重试。")
            return 4
        if guard_state.get("loginBtn") or not guard_state.get("logged"):
            print("\n[!] 登录态已失效，需要重新扫码")
            return 2

        raw = json.loads(evaluate(ws, EXTRACT) or "[]")
        print(f"\n采集到 {len(raw)} 个职位\n")

        from src.boss.collector import _extract_job_id, _split_area

        jobs = []
        for r in raw:
            city, district = _split_area(r["area"])
            jobs.append(Job(
                job_id=_extract_job_id(r["href"]) or r["href"],
                title=r["title"], company=r["company"],
                salary=r["salary"], city=city,
                tags=tuple(r["tags"]), url=r["href"],
            ))

        # 薪资可信度
        guard = SalaryGuard()
        guard.feed([j.salary for j in jobs])
        print(f"薪资状态: {guard.describe()}\n")

        # 用当前配置规则筛选
        from src.boss.config import load_config
        from src.boss.filters import screen

        cfg = load_config("config.yaml")
        print(f"当前规则: title_any={cfg.rules.title_any}, "
              f"min_salary_k={cfg.rules.min_salary_k}, "
              f"cities={cfg.rules.cities_allow}, "
              f"exclude={cfg.rules.exclude_keywords}")

        passed, skipped = screen(jobs, cfg.rules, frozenset())

        print(f"\n{'='*64}")
        print(f"将会投递 {len(passed)} 个 / 跳过 {len(skipped)} 个")
        print(f"{'='*64}\n")
        for j in passed:
            ob = " [薪资不可读]" if is_obfuscated(j.salary) else ""
            print(f"  ▶ {j.title[:22]:24} {j.company[:14]:16} "
                  f"{j.city[:6]:8}{ob}")
        print()
        for j, reason in skipped[:8]:
            print(f"  ✗ {j.title[:22]:24} {reason}")

        if passed:
            print(f"\n首个候选:")
            t = passed[0]
            print(f"  职位: {t.title}")
            print(f"  公司: {t.company}")
            print(f"  薪资: {t.salary!r}"
                  f"{' (字体混淆，读不出)' if is_obfuscated(t.salary) else ''}")
            print(f"  地点: {t.city}")
            print(f"  链接: https://www.zhipin.com{t.url}")

        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    raise SystemExit(main())
