"""命令行入口 + 流水线编排。

一次运行走完整条链路：登录检查 → 采集 → 筛选 → 投递 → 日报 → 通知。
幂等性由 state 层保证：崩了重跑不会重复投递，任何中断都留下可解释的记录。
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import auth, collector, models, reporter
from .applier import apply_to_jobs
from .browser import BrowserSession, detect_page_state
from .config import ConfigError, StartupGuide, load_config
from .filters import screen
from .models import (
    EXIT_CAPTCHA,
    EXIT_LOGIN_REQUIRED,
    EXIT_OK,
    EXIT_SELECTORS_BROKEN,
    EXIT_UNEXPECTED,
    ApplyRecord,
    CaptchaError,
    DependencyError,
    LoginRequiredError,
    RunResult,
    RunStats,
    SelectorsBrokenError,
    SkipRecord,
)
from .state import CST, State


def _logger():
    def log(msg: str) -> None:
        print(msg, flush=True)
    return log


def cmd_login(args, cfg, logger) -> int:
    auth.interactive_login(cfg, logger)
    return EXIT_OK


def cmd_capture_filters(args, cfg, logger) -> int:
    url = auth.capture_filters(cfg, logger)
    if url and "zhipin.com" in url:
        auth.write_search_url(cfg, url)
        logger(f"[ok] 已写入 config.yaml 的 source.search_url")
        return EXIT_OK
    logger("[warn] 捕获到的 URL 看起来不对，未写入配置")
    return EXIT_UNEXPECTED


def cmd_run(args, cfg, logger) -> int:
    """主流水线。"""
    dry_run = cfg.safety.dry_run if args.dry_run is None else args.dry_run
    cfg.safety.dry_run = dry_run

    logger("=" * 60)
    logger(f"BOSS 自动投递启动 · 模式: {'干跑（不实际投递）' if dry_run else '实际投递'}")
    logger("=" * 60)

    result = RunResult(stats=RunStats(dry_run=dry_run))
    result.stats.started_at = datetime.now(CST).isoformat(timespec="seconds")

    db_path = cfg.path(cfg.db_path)
    state = State(db_path)
    run_id = state.start_run(dry_run=dry_run)

    try:
        with BrowserSession(cfg, logger) as session:
            page = session.new_page()

            # --- 1. 登录检查 ---
            logger("[step 1/5] 检查登录态")
            try:
                auth.ensure_login(page, cfg, logger)
            except LoginRequiredError:
                if cfg.relogin.enabled:
                    if auth.send_qrcode_for_relogin(page, cfg, logger):
                        logger("[ok] 重登成功，继续本轮任务")
                    else:
                        raise
                else:
                    logger("[error] 登录失效且未启用重登")
                    raise
            page.close()

            # --- 2. 采集 ---
            logger("[step 2/5] 采集职位")
            jobs = collector.collect(session, cfg, logger, max_pages=args.max_pages)
            result.stats.collected = len(jobs)

            # --- 3. 筛选 ---
            logger("[step 3/5] 筛选")
            applied_ids = state.applied_ids()
            passed, skipped = screen(jobs, cfg.rules, applied_ids)
            result.skipped = [SkipRecord(job=j, reason=r) for j, r in skipped]
            for j, r in skipped:
                state.record_skip(j, r)
            for j in passed:
                state.record_seen(j)
            logger(f"[info] 通过 {len(passed)} / 跳过 {len(skipped)}")

            # --- 4. 投递 ---
            logger("[step 4/5] 投递")
            if passed:
                stats = apply_to_jobs(session, cfg, state, passed, logger)
                result.stats.applied = stats.applied
                result.stats.failed += stats.failed
                if stats.stopped_reason:
                    result.stats.stopped_reason = stats.stopped_reason
                result.applied = [
                    ApplyRecord(job=j, success=True, message="成功")
                    for j in passed[: stats.applied]
                ]
            else:
                result.stats.stopped_reason = "没有通过筛选的职位"
                logger("[info] 没有通过筛选的职位")

    except CaptchaError as e:
        result.error = f"遇到滑块验证码，已停止：{e}"
        result.stats.stopped_reason = "验证码"
        logger(f"[error] {result.error}")
        return _finalize(result, cfg, state, run_id, logger, EXIT_CAPTCHA)
    except LoginRequiredError as e:
        result.error = f"登录已失效，需要重新扫码：{e}"
        logger(f"[error] {result.error}")
        return _finalize(result, cfg, state, run_id, logger, EXIT_LOGIN_REQUIRED)
    except DependencyError as e:
        # 环境问题：给命令，不给 traceback
        print(f"\n[环境缺少依赖] {e}\n", file=sys.stderr)
        return EXIT_UNEXPECTED
    except SelectorsBrokenError as e:
        result.error = f"页面结构变化，选择器失效：{e}"
        logger(f"[error] {result.error}")
        return _finalize(result, cfg, state, run_id, logger, EXIT_SELECTORS_BROKEN)
    except ConfigError as e:
        print(f"[配置错误] {e}", file=sys.stderr)
        return EXIT_UNEXPECTED
    except KeyboardInterrupt:
        result.error = "被用户中断"
        logger("[warn] 已中断")
        return _finalize(result, cfg, state, run_id, logger, EXIT_UNEXPECTED)
    except Exception as e:  # noqa: BLE001 - 顶层兜底，必须留下 traceback
        result.error = f"{type(e).__name__}: {e}"
        logger(f"[error] 未预期异常:\n{traceback.format_exc()}")
        return _finalize(result, cfg, state, run_id, logger, EXIT_UNEXPECTED)

    return _finalize(result, cfg, state, run_id, logger, EXIT_OK)


def _finalize(result, cfg, state, run_id, logger, exit_code: int) -> int:
    """统一的收尾：写统计 + 出报告 + 关库。"""
    try:
        result.stats.finished_at = datetime.now(CST).isoformat(timespec="seconds")
        state.finish_run(run_id, result.stats, error=result.error)
        report_path = reporter.write_report(result, cfg, state)
        logger(f"[ok] 日报已生成: {report_path}")
        reporter.notify(result, cfg, state, report_path, logger)
    except Exception as e:
        logger(f"[warn] 收尾阶段出错: {e}")
    finally:
        state.close()

    s = result.stats
    logger("=" * 60)
    logger(f"完成 · 采集 {s.collected} · 投递 {s.applied} · 跳过 {s.skipped} "
           f"· 失败 {s.failed} · 退出码 {exit_code}")
    logger("=" * 60)
    return exit_code


def cmd_demo_report(args, cfg, logger) -> int:
    """生成一份日报样例。没有账号也能确认报告格式。"""
    from pathlib import Path

    from .demo import build_demo_html

    out_dir = Path(cfg.report.output_dir) if cfg else Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "demo-report.html"
    path.write_text(build_demo_html(), encoding="utf-8")
    logger(f"[ok] 样例日报已生成: {path}")
    logger("[info] 用浏览器打开即可查看")
    return EXIT_OK


def cmd_status(args, cfg, logger) -> int:
    """查看额度与统计。"""
    state = State(cfg.path(cfg.db_path))
    try:
        summary = state.stats_summary()
        remaining = state.remaining_today(cfg.schedule.daily_cap)
        print(f"今日已投递 : {summary['today']} / {cfg.schedule.daily_cap}")
        print(f"今日剩余   : {remaining}")
        print(f"单窗上限   : {cfg.schedule.per_window_cap}")
        print(f"累计投递   : {summary['total_applied']}")
        print(f"干跑模式   : {'是' if cfg.safety.dry_run else '否（会实际投递）'}")
        print(f"时间窗     : {', '.join(cfg.schedule.windows)}")
        print()
        recent = state.recent_applied(limit=args.limit)
        if recent:
            print("最近投递：")
            for r in recent:
                print(f"  {r['applied_at']}  {r['title']} @ {r['company']} ({r['salary']})")
        else:
            print("还没有投递记录")
        return EXIT_OK
    finally:
        state.close()


def cmd_check_selectors(args, cfg, logger) -> int:
    """校验页面选择器是否仍有效 —— 网站改版的早期预警。"""
    logger("[info] 检查页面选择器")
    try:
        with BrowserSession(cfg, logger) as session:
            page = session.new_page()
            url = cfg.source.search_url or "https://www.zhipin.com/web/geek/jobs"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            detector_state = detect_page_state(page)
            logger(f"[info] 页面状态: {detector_state}")
            if detector_state == "captcha":
                logger("[warn] 遇到验证码，无法完成检查")
                return EXIT_CAPTCHA
            if detector_state == "login_required":
                logger("[warn] 登录已失效，请先登录")
                return EXIT_LOGIN_REQUIRED

            from .browser import SELECTORS, first_visible
            broken = []
            for name, candidates in SELECTORS.items():
                if name in ("captcha_marker", "qrcode", "login_marker",
                            "already_chat", "next_page"):
                    continue    # 这些只在特定状态出现，缺席不算坏
                if first_visible(page, candidates, timeout_ms=1500) is None:
                    broken.append(name)
            if broken:
                logger(f"[warn] 以下选择器全部失效，页面可能已改版: {', '.join(broken)}")
                return EXIT_SELECTORS_BROKEN
            logger("[ok] 关键选择器全部正常")
            return EXIT_OK
    except Exception as e:
        logger(f"[error] 检查失败: {e}")
        return EXIT_UNEXPECTED


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="boss-auto-apply",
        description="BOSS 直聘自动筛选与投递工具",
    )
    p.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="首次登录，保存浏览器 Profile")
    sub.add_parser("capture-filters", help="录制网页端筛选条件")

    run = sub.add_parser("run", help="执行一次采集+筛选+投递")
    run.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                     help="只出不投")
    run.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                     help="覆盖配置为实际投递")
    run.add_argument("--max-pages", type=int, default=None, help="覆盖 source.max_pages")

    st = sub.add_parser("status", help="查看额度与统计")
    st.add_argument("--limit", type=int, default=20, help="显示最近几条投递")

    sub.add_parser("demo-report", help="生成一份日报样例")
    sub.add_parser("check-selectors", help="校验选择器是否失效")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = _logger()

    # demo-report 不需要完整配置：它只是本地出一张样例图
    cfg = None
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        if args.command == "demo-report":
            pass
        else:
            # 配置没准备好时，别只抛错 —— 告诉用户下一步做什么
            print(f"\n[配置还没准备好] {e}", file=sys.stderr)
            print(StartupGuide.render(), file=sys.stderr)
            return EXIT_UNEXPECTED

    if cfg is None and args.command != "demo-report":
        # 配置文件本身不存在（load_config 已经报过并return，这里是双保险）
        return EXIT_UNEXPECTED

    handlers = {
        "login": cmd_login,
        "capture-filters": cmd_capture_filters,
        "run": cmd_run,
        "status": cmd_status,
        "check-selectors": cmd_check_selectors,
        "demo-report": cmd_demo_report,
    }
    return handlers[args.command](args, cfg, logger)


if __name__ == "__main__":
    sys.exit(main())
