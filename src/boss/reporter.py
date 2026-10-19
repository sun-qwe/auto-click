"""日报生成与推送。

HTML 报告是主产物（永远生成），邮件和 webhook 是可选推送（失败不影响主流程）。
通知失败必须降级为警告 —— 日报没发出来，比起投递失败，优先级低一个量级。
"""

from __future__ import annotations

import html
import json
import smtplib
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from .state import CST, State

CST_TZ = CST


def render_html(result, cfg, state: State) -> str:
    """生成单文件 HTML 日报。无外部依赖，双击就能开。"""
    stats = result.stats
    rows_applied = "".join(
        f"""<tr>
          <td>{_esc(r.job.title)}</td>
          <td>{_esc(r.job.company)}</td>
          <td>{_esc(r.job.salary)}</td>
          <td>{_esc(r.job.city)}</td>
          <td>{'✅' if r.success else '❌'}</td>
          <td>{_esc(r.message)}</td>
          <td><a href="{_esc(r.job.url)}" target="_blank">打开</a></td>
        </tr>"""
        for r in result.applied
    ) or '<tr><td colspan="7" class="empty">本次没有投递</td></tr>'

    rows_skipped = "".join(
        f"""<tr>
          <td>{_esc(r.job.title)}</td>
          <td>{_esc(r.job.company)}</td>
          <td>{_esc(r.job.salary)}</td>
          <td class="reason">{_esc(r.reason)}</td>
          <td><a href="{_esc(r.job.url)}" target="_blank">打开</a></td>
        </tr>"""
        for r in result.skipped
    ) or '<tr><td colspan="5" class="empty">没有被跳过的职位</td></tr>'

    summary = state.stats_summary()
    dry_badge = ('<span class="badge dry">干跑模式（未实际投递）</span>'
                 if stats.dry_run else '<span class="badge live">实际投递</span>')
    error_block = (
        f'<div class="alert error"><strong>异常：</strong>{_esc(result.error)}</div>'
        if result.error else ""
    )
    stop_block = (
        f'<div class="alert warn"><strong>停止原因：</strong>{_esc(stats.stopped_reason)}</div>'
        if stats.stopped_reason else ""
    )
    now = datetime.now(CST_TZ).strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOSS 自动投递日报 {now}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 24px; line-height: 1.6; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .meta {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
  .card {{ flex: 1; min-width: 120px; border: 1px solid #ddd; border-radius: 8px;
           padding: 14px; text-align: center; }}
  .card .num {{ font-size: 26px; font-weight: 600; }}
  .card .label {{ font-size: 12px; color: #888; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
  th, td {{ border: 1px solid #e3e3e3; padding: 8px 10px; text-align: left; }}
  th {{ background: #f6f7f9; font-weight: 600; }}
  tr:nth-child(even) td {{ background: #fafbfc; }}
  .reason {{ color: #b06000; }}
  .empty {{ text-align: center; color: #999; padding: 18px; }}
  h2 {{ font-size: 17px; margin-top: 28px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px;
            font-size: 12px; }}
  .badge.dry {{ background: #fff4e5; color: #b06000; border: 1px solid #f0c98a; }}
  .badge.live {{ background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; }}
  .alert {{ padding: 10px 14px; border-radius: 6px; margin: 12px 0; }}
  .alert.error {{ background: #fdecea; border: 1px solid #f5c2bd; color: #a32b1c; }}
  .alert.warn {{ background: #fff8e1; border: 1px solid #ffe082; color: #8a6100; }}
  a {{ color: #1677ff; }}
</style>
</head>
<body>
  <h1>BOSS 直聘自动投递日报 {dry_badge}</h1>
  <div class="meta">生成于 {now} · 累计已投递 {summary['total_applied']} · 今日 {summary['today']}</div>

  {error_block}
  {stop_block}

  <div class="cards">
    <div class="card"><div class="num">{stats.collected}</div><div class="label">采集职位</div></div>
    <div class="card"><div class="num">{stats.applied}</div><div class="label">投递/通过</div></div>
    <div class="card"><div class="num">{stats.skipped}</div><div class="label">跳过</div></div>
    <div class="card"><div class="num">{stats.failed}</div><div class="label">失败</div></div>
  </div>

  <h2>投递明细</h2>
  <table>
    <tr><th>职位</th><th>公司</th><th>薪资</th><th>城市</th><th>结果</th><th>说明</th><th>链接</th></tr>
    {rows_applied}
  </table>

  <h2>跳过明细（含原因）</h2>
  <table>
    <tr><th>职位</th><th>公司</th><th>薪资</th><th>原因</th><th>链接</th></tr>
    {rows_skipped}
  </table>
</body>
</html>
"""


def write_report(result, cfg, state: State) -> Path:
    """写日报到 reports/ 并返回路径。"""
    out_dir = cfg.path(cfg.report.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CST_TZ).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"report-{stamp}.html"
    path.write_text(render_html(result, cfg, state), encoding="utf-8")
    prune_old_reports(cfg)
    return path


def prune_old_reports(cfg) -> None:
    """清理超过保留天数的旧日报。"""
    out_dir = cfg.path(cfg.report.output_dir)
    if not out_dir.exists():
        return
    cutoff = datetime.now(CST_TZ) - timedelta(days=cfg.report.keep_days)
    for p in out_dir.glob("report-*.html"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, CST_TZ)
            if mtime < cutoff:
                p.unlink()
        except Exception:
            continue


def notify(result, cfg, state: State, report_path: Path, logger) -> None:
    """推送通知。任一通道失败只记日志，不影响主流程。"""
    if cfg.report.email.enabled:
        try:
            _send_email_report(cfg, result, report_path)
            logger("[info] 日报邮件已发送")
        except Exception as e:
            logger(f"[warn] 邮件发送失败（不影响投递）: {e}")

    if cfg.report.webhook.enabled:
        try:
            _send_webhook(cfg, result, report_path)
            logger("[info] webhook 推送成功")
        except Exception as e:
            logger(f"[warn] webhook 推送失败（不影响投递）: {e}")


def _summary_text(result) -> str:
    s = result.stats
    mode = "干跑" if s.dry_run else "实际投递"
    lines = [
        f"BOSS 自动投递日报（{mode}）",
        f"采集 {s.collected} · 投递 {s.applied} · 跳过 {s.skipped} · 失败 {s.failed}",
    ]
    if s.stopped_reason:
        lines.append(f"停止原因：{s.stopped_reason}")
    if result.error:
        lines.append(f"异常：{result.error}")
    return "\n".join(lines)


def _send_email_report(cfg, result, report_path: Path) -> None:
    em = cfg.report.email
    if not em.smtp_host or not em.to:
        raise RuntimeError("邮件配置不完整")

    msg = EmailMessage()
    msg["Subject"] = f"[BOSS 自动投递] {_summary_line(result)}"
    msg["From"] = em.from_addr or em.username
    msg["To"] = ", ".join(em.to)
    msg.set_content(_summary_text(result))

    html_body = Path(report_path).read_text(encoding="utf-8")
    msg.add_alternative(html_body, subtype="html")

    if em.use_ssl:
        server = smtplib.SMTP_SSL(em.smtp_host, em.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(em.smtp_host, em.smtp_port, timeout=30)
        server.starttls()
    try:
        if em.username:
            server.login(em.username, em.password)
        server.send_message(msg)
    finally:
        server.quit()


def _summary_line(result) -> str:
    s = result.stats
    return f"采集{s.collected} 投递{s.applied} 跳过{s.skipped} 失败{s.failed}"


def _send_webhook(cfg, result, report_path: Path) -> None:
    """推送到钉钉 / 企微 / 飞书机器人。

    三种机器人接受的 JSON 结构不同，这里用最通用的 text markdown 双字段，
    钉钉和飞书可接受，企微用 text 也能显示。
    """
    url = cfg.report.webhook.url
    if not url:
        raise RuntimeError("未配置 webhook 地址")

    s = result.stats
    content = _summary_text(result)
    details = "\n".join(
        f"- {r.job.title} @ {r.job.company} ({r.job.salary})" for r in result.applied[:10]
    )
    if details:
        content += "\n\n投递明细：\n" + details

    payload = {
        "msgtype": "text",
        "text": {"content": content},
        "markdown": {"title": "BOSS 自动投递日报", "text": content},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"webhook 返回 {resp.status}")


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=True)
