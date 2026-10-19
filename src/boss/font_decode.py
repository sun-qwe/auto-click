"""BOSS 自定义字体（kanzhun-mix）的 PUA 数字混淆处理。

诚实结论
--------
BOSS 把薪资数字渲染成私有区码点（U+E0xx），字体负责显示成正常数字，
但 DOM 里的文本是乱码。这是**有意设计的反爬**，而且映射会轮换。

我尝试过三种破解，都失败了：
  1. Canvas 像素指纹 —— kanzhun 字体里的数字字形被刻意改过，
     渲染出来和常规字体对不上，指纹匹配失败
  2. 解析字体文件 cmap —— 页面上用的是 JS 动态注入的字体，
     能抓到的公开 ttf（2022 年）不含 PUA 区，是旧文件
  3. 频次/约束推断 —— 用"最高频=1""上限>=下限"做搜索，
     解出来的结果自相矛盾（标题明示 10k+ 的职位被解成 20K）

结论：**不要试图在客户端精确还原薪资数值。**
强行猜一个映射并用于筛选，会静默地投错职位 —— 比不用更糟。

本模块因此只做两件事
--------------------
1. 检测混淆是否存在（这事能可靠做到）
2. 混淆存在时，明确告诉上层"薪资不可信"，由上层决定降级策略

降级策略由 config.yaml 的 rules.on_obfuscated_salary 控制：
    passthrough  放行（当作面议）—— 默认，宁可多投不误杀
    skip         整条跳过 —— 保守，适合你对薪资下限很在意时
    warn_only    放行但在日报里标注
"""

from __future__ import annotations

import re

PUA_START, PUA_END = 0xE000, 0xF8FF


def is_obfuscated(text: str) -> bool:
    """文本里是否含有私有区码点（即薪资被字体混淆了）。"""
    if not text:
        return False
    return any(PUA_START <= ord(c) <= PUA_END for c in text)


def pua_codepoints(text: str) -> list[str]:
    """列出文本里所有 PUA 码点，便于诊断和日志记录。"""
    return [f"U+{ord(c):04X}" for c in text
            if PUA_START <= ord(c) <= PUA_END]


def digits_are_plain(text: str) -> bool:
    """薪资里的数字是否是明文（未被混淆）。"""
    return bool(re.search(r"\d", text)) and not is_obfuscated(text)


class SalaryGuard:
    """在一批职位上判断薪资是否可信，并给出处置建议。

    用法：
        guard = SalaryGuard()
        guard.feed([job.salary for job in jobs])
        if not guard.salary_trustworthy:
            # 薪资不可信，按 rules.on_obfuscated_salary 降级
    """

    def __init__(self):
        self.total = 0
        self.obfuscated = 0
        self.plain = 0

    def feed(self, salary_texts: list[str]) -> None:
        for t in salary_texts:
            if not t:
                continue
            self.total += 1
            if is_obfuscated(t):
                self.obfuscated += 1
            elif re.search(r"\d", t):
                self.plain += 1

    @property
    def salary_trustworthy(self) -> bool:
        """薪资能否用于数值筛选。

        只要有一条被混淆，就认为整体不可信 —— 因为你无法知道
        哪些是真面议、哪些是被混淆后误判成面议的。
        """
        return self.obfuscated == 0 and self.plain > 0

    @property
    def obfuscation_ratio(self) -> float:
        if not self.total:
            return 0.0
        return self.obfuscated / self.total

    def describe(self) -> str:
        if self.salary_trustworthy:
            return "薪资明文，可直接用于数值筛选"
        return (f"检测到 {self.obfuscated}/{self.total} 条薪资被字体混淆"
                f"（{self.obfuscation_ratio:.0%}），数值筛选不可靠")


def build_guard(salary_texts: list[str]) -> SalaryGuard:
    g = SalaryGuard()
    g.feed(salary_texts)
    return g
