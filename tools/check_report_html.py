"""校验生成的日报 HTML 内容是否完整。

不做视觉校验，只断言关键结构和数据都在。
"""

import sys
from pathlib import Path

# 让脚本可以直接 `python tools/xxx.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "cdp"))

import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent
HTML = ROOT / "reports" / "demo-report.html"


def main() -> int:
    if not HTML.exists():
        print(f"[skip] 未找到 {HTML}，先跑 m src.boss.cli demo-report")
        return 0

    text = HTML.read_text(encoding="utf-8")
    body = re.sub(r"<style>.*?</style>", "", text, flags=re.S)

    checks = {
        "标题存在": "BOSS 直聘自动投递日报" in text,
        "干跑标记": "干跑模式" in text,
        "投递明细表": "投递明细" in text,
        "跳过明细表": "跳过明细" in text,
        "跳过原因逐条可见": all(
            k in text for k in ("排除词命中", "薪资下限", "不在白名单", "标题未命中")
        ),
        "四个统计卡片": text.count('class="card"') == 4,
        "模板无残留花括号": "{" not in body,
        "职位链接正确": "job_detail/demo1.html" in text,
        "中文未乱码": "Java开发工程师" in text,
    }

    for name, ok in checks.items():
        print(("  OK   " if ok else " FAIL  ") + name)

    if all(checks.values()):
        print(f"\n[ok] 日报内容校验通过（{len(checks)} 项）")
        return 0
    print("\n[fail] 有校验项未通过")
    return 1


if __name__ == "__main__":
    sys.exit(main())
