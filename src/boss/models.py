"""领域数据模型。

这些结构在 collector / filters / applier / reporter 之间流转，
是各模块之间唯一的耦合点。保持它们贫瘠（只有数据，没有行为），
筛选逻辑一律放进 filters.py 的纯函数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# 退出码：cron / systemd 依赖这些值判断是否需要告警
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_LOGIN_REQUIRED = 2
EXIT_SELECTORS_BROKEN = 3
EXIT_CAPTCHA = 4

# 薪资区间。必须容忍单位字母与空格，"18k-30k" 这类小写写法很常见，
# 早期版本漏掉这一步导致薪资下限被静默绕过。
_SALARY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[kK千]?\s*[-~至]\s*(\d+(?:\.\d+)?)"
)


class BossError(Exception):
    """所有本项目异常的基类。"""

    exit_code = EXIT_UNEXPECTED


class DependencyError(BossError):
    """运行环境缺少依赖（库 / 浏览器内核 / Xvfb）。

    这是"环境没准备好"而不是"程序有 bug"，必须给出安装命令，
    而不是甩一个 traceback 给用户去猜。
    """

    exit_code = EXIT_UNEXPECTED


class LoginRequiredError(BossError):
    """登录态失效，需要重新扫码。"""

    exit_code = EXIT_LOGIN_REQUIRED


class CaptchaError(BossError):
    """出现滑块验证码。禁止重试 —— 重试会加速风控升级。"""

    exit_code = EXIT_CAPTCHA


class SelectorsBrokenError(BossError):
    """页面结构变化，选择器失效（网站改版）。"""

    exit_code = EXIT_SELECTORS_BROKEN


@dataclass(frozen=True)
class Job:
    """一条职位卡片。

    collector 从搜索结果列表中提取；字段缺失是常态（BOSS 不同版式
    字段不一致），所以除 job_id 外全部可空，由 filters 决定怎么处理。
    """

    job_id: str
    title: str = ""
    company: str = ""
    salary: str = ""          # 原始文本，如 "20-35K"
    city: str = ""
    district: str = ""
    experience: str = ""
    education: str = ""
    tags: tuple[str, ...] = ()
    url: str = ""

    def salary_range_k(self) -> Optional[tuple[float, float]]:
        """把 "20-35K" / "面议" / "" 解析成 (下限, 上限) 千元每月。

        无法解析返回 None（例如"面议"）。支持 15k-25k、20-30K·13薪 等写法。
        """
        return parse_salary(self.salary)


@dataclass
class Verdict:
    """筛选判定结果。

    passed 为 False 时必须带 reason —— 日报要能逐条解释"为什么跳过"，
    否则全自动模式下出问题无法定位。
    """

    passed: bool
    reason: str = ""

    @classmethod
    def ok(cls) -> "Verdict":
        return cls(passed=True)

    @classmethod
    def reject(cls, reason: str) -> "Verdict":
        return cls(passed=False, reason=reason)


@dataclass
class RunStats:
    """一轮运行的统计，日报的骨架。"""

    collected: int = 0
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = True
    stopped_reason: str = ""
    started_at: str = ""
    finished_at: str = ""

    def as_dict(self) -> dict:
        return dict(
            collected=self.collected,
            applied=self.applied,
            skipped=self.skipped,
            failed=self.failed,
            dry_run=self.dry_run,
            stopped_reason=self.stopped_reason,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


@dataclass
class SkipRecord:
    """被跳过的职位，含原因。"""

    job: Job
    reason: str


@dataclass
class ApplyRecord:
    """一次投递动作的结果。"""

    job: Job
    success: bool
    message: str = ""


def parse_salary(text: str) -> Optional[tuple[float, float]]:
    """解析薪资文本为 (下限K, 上限K)。

    >>> parse_salary("20-35K")
    (20.0, 35.0)
    >>> parse_salary("面议") is None
    True
    >>> parse_salary("15k-25k·13薪")
    (15.0, 25.0)
    """
    if not text:
        return None
    m = _SALARY_RE.search(text)
    if not m:
        return None
    low, high = float(m.group(1)), float(m.group(2))

    # 元 -> 千元
    if "元" in text or low > 1000:
        low, high = low / 1000.0, high / 1000.0
    return (low, high)


@dataclass
class RunResult:
    """一轮运行的完整产出，交给 reporter。"""

    stats: RunStats = field(default_factory=RunStats)
    applied: list[ApplyRecord] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def exit_code(self) -> int:
        return EXIT_OK if self.error is None else EXIT_UNEXPECTED
