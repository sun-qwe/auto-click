# tools/ —— 验证与诊断工具

这些脚本不参与日常投递。业务入口是：

```bash
python -m src.boss.cli ...
```

## 静态检查

```bash
python tools/check_refs.py
python tools/check_shell.py
python tools/check_report_html.py
```

## 浏览器验证（需要 Chrome CDP 端口 9222）

| 脚本 | 作用 | 改动账号 |
| --- | --- | --- |
| `smoke_browser.py` | 假站点采集、筛选、点击与去重冒烟 | 否 |
| `dryrun_live.py` | 真实页面采集并列出候选 | 否 |
| `verify_live.py` | 验证真实选择器和 job_id 提取 | 否 |

这里不保留硬编码的真实投递脚本；真实投递统一走正式 CLI，避免误投和污染数据库。

## 辅助代码

- `cdp/cdp.py`：纯标准库 CDP 客户端，仅供上述工具使用。
- `mock_site.py`：生成 BOSS 风格的本地假页面，供浏览器冒烟使用。
