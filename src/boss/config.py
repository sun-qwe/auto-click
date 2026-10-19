"""配置加载与校验。

原则：必填项缺失时**立即失败并给出人话报错**，绝不使用可能出错的默认值。
一个静默的错误默认值，在全自动模式下等于一夜海投。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_CONFIG_PATH = "config.yaml"


class ConfigError(Exception):
    """配置缺失或不合法。"""


@dataclass
class Rules:
    title_any: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    company_blacklist: list[str] = field(default_factory=list)
    min_salary_k: float = 0
    cities_allow: list[str] = field(default_factory=list)
    skip_if_applied_before: bool = True
    # 显式声明"不设任何本地收紧条件"，绕过 validate 中的保护
    allow_unfiltered: bool = False
    # BOSS 用自定义字体把薪资渲染成私有区码点，数值不可信
    # （见 font_decode.py，三种破解都失败）。遇到混淆薪资时怎么办：
    #   passthrough  当面议放行（默认）—— 宁可多投，不误杀好职位
    #   skip         整条跳过 —— 适合你对薪资下限很在意时
    #   warn_only    放行，但日报里标注
    on_obfuscated_salary: str = "passthrough"

    def is_unfiltered(self) -> bool:
        """是否完全没有收紧条件。"""
        return not self.title_any and not self.min_salary_k and not self.cities_allow


@dataclass
class Runtime:
    profile_dir: str = "state/browser-profile"
    headless: bool = False
    xvfb_display: str = ":99"
    slow_mo_ms: int = 120
    page_timeout_ms: int = 30000
    viewport_width: int = 1440
    viewport_height: int = 900
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"


@dataclass
class Source:
    search_url: str = ""
    max_pages: int = 3


@dataclass
class Schedule:
    windows: list[str] = field(default_factory=lambda: ["09:30"])
    per_window_cap: int = 8
    daily_cap: int = 24
    min_interval_sec: list[int] = field(default_factory=lambda: [20, 60])
    page_interval_sec: list[int] = field(default_factory=lambda: [3, 8])


@dataclass
class Safety:
    dry_run: bool = True
    stop_on_captcha: bool = True
    max_consecutive_failures: int = 3
    job_action_timeout_sec: int = 25


@dataclass
class Relogin:
    enabled: bool = True
    channel: str = "email"
    wait_timeout_sec: int = 600
    poll_interval_sec: int = 5


@dataclass
class EmailNotify:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    use_ssl: bool = True
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to: list[str] = field(default_factory=list)


@dataclass
class WebhookNotify:
    enabled: bool = False
    url: str = ""


@dataclass
class ReportConfig:
    output_dir: str = "reports"
    keep_days: int = 30
    email: EmailNotify = field(default_factory=EmailNotify)
    webhook: WebhookNotify = field(default_factory=WebhookNotify)


@dataclass
class Config:
    root: Path = field(default=Path("."))
    runtime: Runtime = field(default_factory=Runtime)
    source: Source = field(default_factory=Source)
    rules: Rules = field(default_factory=Rules)
    schedule: Schedule = field(default_factory=Schedule)
    safety: Safety = field(default_factory=Safety)
    relogin: Relogin = field(default_factory=Relogin)
    report: ReportConfig = field(default_factory=ReportConfig)
    db_path: str = "state/boss.db"

    def path(self, relative: str) -> Path:
        """把配置里的相对路径解析成绝对路径。"""
        p = Path(relative)
        return p if p.is_absolute() else (self.root / p).resolve()


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    """读取并校验配置文件。任何必填项缺失都抛 ConfigError。"""
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ConfigError(
            f"找不到配置文件 {cfg_path}\n"
            f"      请先执行：cp config.example.yaml config.yaml"
        )

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    root = cfg_path.parent.resolve()

    runtime_raw = raw.get("runtime") or {}
    runtime = Runtime(
        profile_dir=runtime_raw.get("profile_dir", "state/browser-profile"),
        headless=bool(runtime_raw.get("headless", False)),
        xvfb_display=str(runtime_raw.get("xvfb_display", ":99")),
        slow_mo_ms=int(runtime_raw.get("slow_mo_ms", 120)),
        page_timeout_ms=int(runtime_raw.get("page_timeout_ms", 30000)),
        viewport_width=int((runtime_raw.get("viewport") or {}).get("width", 1440)),
        viewport_height=int((runtime_raw.get("viewport") or {}).get("height", 900)),
        locale=runtime_raw.get("locale", "zh-CN"),
        timezone=runtime_raw.get("timezone", "Asia/Shanghai"),
    )

    source_raw = raw.get("source") or {}
    source = Source(
        search_url=(source_raw.get("search_url") or "").strip(),
        max_pages=int(source_raw.get("max_pages", 3)),
    )

    rules_raw = raw.get("rules") or {}
    rules = Rules(
        title_any=list(rules_raw.get("title_any") or []),
        exclude_keywords=list(rules_raw.get("exclude_keywords") or []),
        company_blacklist=list(rules_raw.get("company_blacklist") or []),
        min_salary_k=float(rules_raw.get("min_salary_k") or 0),
        cities_allow=list(rules_raw.get("cities_allow") or []),
        skip_if_applied_before=bool(rules_raw.get("skip_if_applied_before", True)),
        allow_unfiltered=bool(rules_raw.get("allow_unfiltered", False)),
        on_obfuscated_salary=str(
            rules_raw.get("on_obfuscated_salary", "passthrough")),
    )

    sch_raw = raw.get("schedule") or {}
    schedule = Schedule(
        windows=list(sch_raw.get("windows") or ["09:30"]),
        per_window_cap=int(sch_raw.get("per_window_cap", 8)),
        daily_cap=int(sch_raw.get("daily_cap", 24)),
        min_interval_sec=_pair(sch_raw.get("min_interval_sec"), 20, 60),
        page_interval_sec=_pair(sch_raw.get("page_interval_sec"), 3, 8),
    )

    safety_raw = raw.get("safety") or {}
    safety = Safety(
        dry_run=bool(safety_raw.get("dry_run", True)),
        stop_on_captcha=bool(safety_raw.get("stop_on_captcha", True)),
        max_consecutive_failures=int(safety_raw.get("max_consecutive_failures", 3)),
        job_action_timeout_sec=int(safety_raw.get("job_action_timeout_sec", 25)),
    )

    relogin_raw = raw.get("relogin") or {}
    relogin = Relogin(
        enabled=bool(relogin_raw.get("enabled", True)),
        channel=str(relogin_raw.get("channel", "email")),
        wait_timeout_sec=int(relogin_raw.get("wait_timeout_sec", 600)),
        poll_interval_sec=int(relogin_raw.get("poll_interval_sec", 5)),
    )

    rep_raw = raw.get("report") or {}
    em_raw = (rep_raw.get("notify") or {}).get("email") or {}
    # 密码/用户名优先走环境变量，避免明文入库
    email = EmailNotify(
        enabled=bool(em_raw.get("enabled", False)),
        smtp_host=str(em_raw.get("smtp_host") or ""),
        smtp_port=int(em_raw.get("smtp_port") or 465),
        use_ssl=bool(em_raw.get("use_ssl", True)),
        username=os.environ.get("BOSS_MAIL_USER") or str(em_raw.get("username") or ""),
        password=os.environ.get("BOSS_MAIL_PASS") or str(em_raw.get("password") or ""),
        from_addr=str(em_raw.get("from") or em_raw.get("username") or ""),
        to=list(em_raw.get("to") or []),
    )
    wh_raw = (rep_raw.get("notify") or {}).get("webhook") or {}
    webhook = WebhookNotify(
        enabled=bool(wh_raw.get("enabled", False)),
        url=os.environ.get("BOSS_WEBHOOK_URL") or str(wh_raw.get("url") or ""),
    )
    report = ReportConfig(
        output_dir=str(rep_raw.get("output_dir", "reports")),
        keep_days=int(rep_raw.get("keep_days", 30)),
        email=email,
        webhook=webhook,
    )

    state_raw = raw.get("state") or {}
    cfg = Config(
        root=root,
        runtime=runtime,
        source=source,
        rules=rules,
        schedule=schedule,
        safety=safety,
        relogin=relogin,
        report=report,
        db_path=str(state_raw.get("db_path", "state/boss.db")),
    )
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    """校验关键配置。缺什么都直接说中文原因。"""
    errors: list[str] = []

    if cfg.schedule.daily_cap <= 0:
        errors.append("schedule.daily_cap 必须大于 0")
    if cfg.schedule.per_window_cap <= 0:
        errors.append("schedule.per_window_cap 必须大于 0")
    if cfg.schedule.per_window_cap > cfg.schedule.daily_cap:
        errors.append("schedule.per_window_cap 不能大于 daily_cap")
    lo, hi = cfg.schedule.min_interval_sec
    if lo <= 0 or hi < lo:
        errors.append("schedule.min_interval_sec 必须是 [正数, 不小于前者的数]")
    if cfg.safety.max_consecutive_failures < 1:
        errors.append("safety.max_consecutive_failures 至少为 1")
    if cfg.source.max_pages < 1:
        errors.append("source.max_pages 至少为 1")
    if cfg.rules.on_obfuscated_salary not in ("passthrough", "skip", "warn_only"):
        errors.append(
            "rules.on_obfuscated_salary 只能是 passthrough / skip / warn_only 之一"
        )

    # 没有任何收紧条件时，等于把网页端结果原样投递 —— 通常不是本意，但可以显式承担
    if not cfg.rules.allow_unfiltered and cfg.rules.is_unfiltered():
        errors.append(
            "rules 未填写任何筛选条件（title_any / min_salary_k / cities_allow 全为空）\n"
            "      不设筛选就全自动投递 = 海投，会被平台判定为低质量候选。\n"
            "      请至少填一项，例如：\n"
            '        rules:\n'
            '          title_any: ["Java", "后端"]      # 职位标题包含任一关键词\n'
            '          min_salary_k: 20                # 月薪下限（单位：K）\n'
            '          cities_allow: ["深圳"]           # 只看这些城市\n'
            "      确实不想设任何条件的话，显式写 rules.allow_unfiltered: true 解除本校验。"
        )

    if cfg.source.search_url and "zhipin.com" not in cfg.source.search_url:
        errors.append(
            f"source.search_url 不像 BOSS 的地址：{cfg.source.search_url}\n"
            "      请先运行 `python -m src.boss.cli capture-filters` 自动生成，"
            "或直接填 https://www.zhipin.com/web/geek/jobs?... 这样的网址"
        )

    if errors:
        raise ConfigError("配置还没准备好：\n  - " + "\n  - ".join(errors))


class StartupGuide:
    """首次使用的引导：告诉用户下一步该干什么，而不是只报个错。"""

    STEPS = [
        ("填写筛选规则", "用编辑器打开 config.yaml，填 rules 段（至少一项）"),
        ("首次登录", "python -m src.boss.cli login"),
        ("录制搜索条件", "python -m src.boss.cli capture-filters"),
        ("干跑验证", "python -m src.boss.cli run --dry-run"),
        ("正式启用", "把 config.yaml 里 safety.dry_run 改成 false"),
    ]

    @classmethod
    def render(cls) -> str:
        lines = ["", "第一次使用请按这个顺序来："]
        for i, (name, cmd) in enumerate(cls.STEPS, 1):
            lines.append(f"  {i}. {name:<12} {cmd}")
        lines.append("")
        lines.append("想先看看报告长什么样：python -m src.boss.cli demo-report")
        lines.append("")
        return "\n".join(lines)


def _pair(value: Any, dlo: int, dhi: int) -> list[int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [int(value[0]), int(value[1])]
    return [dlo, dhi]
