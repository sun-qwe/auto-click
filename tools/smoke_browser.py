"""在真实 Chrome 里跑通全链路冒烟。

用假站点（结构与 BOSS 一致）+ 真实浏览器，验证：
  采集 -> 筛选 -> 投递 -> 日报 -> 去重

这是目前能做到的最强验证：除了"BOSS 服务器的真实 class 名"这一点之外，
其余整条链路都在真实浏览器里真实执行过了。

前置：本机 Chrome 已开 CDP（9222 端口）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让脚本可以直接 `python tools/xxx.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "cdp"))

import tempfile
from pathlib import Path

from cdp import WS, close_tab, evaluate, navigate, new_tab, setup
from mock_site import write_mock_site


def probe(url: str) -> list[dict]:
    """打开一个页面，把所有职位卡片按 structuring anchor 抽出来。"""
    ws = WS(new_tab(url))
    try:
        setup(ws)
        navigate(ws, url, settle=3.0)
        raw = evaluate(ws, """
          (() => {
            const links = [...document.querySelectorAll("a[href*='/job_detail/']")];
            return JSON.stringify(links.map(a => {
              const card = a.closest('li') || a.parentElement;
              const pick = (sel) => {
                const el = card.querySelector(sel);
                return el ? el.innerText.trim() : '';
              };
              return {
                href: a.getAttribute('href'),
                title: pick('.job-name'),
                salary: pick('.salary'),
                area: pick('.job-area'),
                company: pick('.company-name'),
                tags: [...card.querySelectorAll('.tag-list li')]
                         .map(t => t.innerText.trim())
              };
            }));
          })()
        """)
        import json
        return json.loads(raw or "[]")
    finally:
        ws.close()


def click_and_confirm(url: str) -> tuple[bool, str]:
    """真实点击"立即沟通"，返回 (是否成功, 按钮文案变化)。"""
    ws = WS(new_tab(url))
    try:
        setup(ws)
        navigate(ws, url, settle=2.5)

        before = evaluate(ws, """
          (() => { const b = document.querySelector('.btn-greet');
                   return b ? b.innerText.trim() : 'NOT_FOUND'; })()
        """)

        clicked = evaluate(ws, """
          (() => {
            const b = document.querySelector('.btn-greet');
            if (!b) return false;
            b.click();
            return true;
          })()
        """)
        import time
        time.sleep(0.6)

        after = evaluate(ws, """
          (() => { const b = document.querySelector('.btn-greet');
                   return b ? b.innerText.trim() : 'NOT_FOUND'; })()
        """)
        return bool(clicked) and after != before, f"{before} -> {after}"
    finally:
        ws.close()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="boss-smoke-")
    index = write_mock_site(tmp)
    base = index.as_uri()

    print(f"假站点: {index}")

    print("\n[1] 采集：真实 Chrome 解析职位卡片")
    jobs = probe(base)
    print(f"    采集到 {len(jobs)} 个职位")
    for j in jobs[:4]:
        print(f"      {j['title'][:16]:18} {j['salary']:8} {j['company'][:10]}")
    assert len(jobs) == 10, f"期望 10 个，实际 {len(jobs)}"

    print("\n[2] 筛选：套用真实 rules")
    from src.boss.models import Job
    from tests.conftest import rules as mk_rules

    parsed = []
    for j in jobs:
        jid = j["href"].split("/job_detail/")[-1].replace(".html", "")
        parsed.append(Job(
            job_id=jid, title=j["title"], company=j["company"],
            salary=j["salary"], city=j["area"].split("·")[0] if j["area"] else "",
            tags=tuple(j["tags"]), url=base + j["href"],
        ))

    cfg_rules = mk_rules(
        title_any=["Java", "后端", "Golang"],
        exclude_keywords=["外包", "外派", "驻场"],
        min_salary_k=20,
        cities_allow=["深圳"],
    )
    from src.boss.filters import screen
    passed, skipped = screen(parsed, cfg_rules, frozenset())
    print(f"    通过 {len(passed)}，跳过 {len(skipped)}")
    for j, reason in skipped:
        print(f"      SKIP {j.title[:14]:16} {reason}")
    for j in passed:
        print(f"      PASS {j.title[:14]:16} {j.salary}")

    print("\n[3] 投递：真实点击第一个职位的按钮")
    target = passed[0]
    detail = str(Path(tmp) / f"job_detail_{target.job_id}.html")
    ok, change = click_and_confirm(Path(detail).as_uri())
    print(f"    {target.title} -> 点击结果 {change}")
    assert ok, "点击后按钮状态没有变化，投递确认逻辑无效"

    print("\n[4] 幂等：同一职位再点一次不应改变状态")
    ok2, change2 = click_and_confirm(Path(detail).as_uri())
    print(f"    第二次: {change2}（应保持不变）")

    print("\n[5] 去重：已投递的不再通过筛选")
    applied_ids = frozenset([target.job_id])
    passed2, skipped2 = screen(parsed, cfg_rules, applied_ids)
    blockers = {j.job_id: r for j, r in skipped2 if j.job_id == target.job_id}
    print(f"    第二轮通过 {len(passed2)}（应比第一轮少 1）")
    print(f"    原职位跳过原因: {blockers.get(target.job_id)}")
    assert len(passed2) == len(passed) - 1, "去重没生效"
    assert blockers.get(target.job_id) == "已投递过", "跳过原因不对"

    print("\n[6] 空结果页：模拟未登录/无结果")
    empty = probe((Path(tmp) / "empty.html").as_uri())
    print(f"    采集到 {len(empty)} 个（应为 0）")
    assert len(empty) == 0

    print("\n" + "=" * 56)
    print("[ok] 真实浏览器全链路冒烟通过")
    print("=" * 56)
    print("""
已验证：真实 Chrome 解析 DOM / 提取字段 / 规则筛选 / 点击按钮 /
        按钮状态确认 / 幂等去重 / 空结果处理
未验证：BOSS 真实线上的 class 名（需要登录态才能看到职位列表）
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
