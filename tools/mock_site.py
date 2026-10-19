"""生成一个与 BOSS 网页端结构一致的假职位列表页，用于在本地做全链路冒烟。

为什么要造假页面：BOSS 未登录时不返回任何职位数据，没法验证采集逻辑。
但结构是已知的（真实 Chrome 渲染过，见 _probe 结果），所以按同样的
容器 class、同样的 /job_detail/ 链接、同样的"立即沟通"按钮造一份，
就能在没有账号的情况下验证 collector -> filters -> applier 全链路。

这不是单元测试（那是 filters 的事），这是"除 BOSS 服务器本身以外，
整条链路在真实浏览器里是否真的能跑通"。
"""

from __future__ import annotations

from pathlib import Path

_CSS = """
 body { font-family: -apple-system, "Microsoft YaHei", sans-serif;
        margin:0; padding:0; background:#f6f7f9; }
 .header-v2 { background:#fff; padding:12px 24px; border-bottom:1px solid #eee; }
 .nav-figure img { width:32px; height:32px; border-radius:50%; }
 .page-jobs-main { max-width:1200px; margin:20px auto; background:#fff;
                   padding:16px; border-radius:6px; }
 .job-search-form { padding:8px 0; border-bottom:1px solid #f0f0f0; }
 .recommend-result-job { list-style:none; margin:0; padding:0; }
 .recommend-result-job li { border-bottom:1px solid #f2f2f2; padding:14px 8px; }
 .job-name { font-size:16px; font-weight:600; color:#222; }
 .company-name { color:#666; font-size:13px; margin-top:4px; }
 .salary { color:#ff6a00; font-weight:600; font-size:15px; }
 .job-area { color:#999; font-size:13px; }
 .tag-list li { display:inline-block; background:#f5f5f5; color:#888;
                font-size:12px; padding:2px 6px; margin-right:6px; border-radius:2px; }
 .detail-op { margin-top:18px; }
 .btn-greet { background:#00a6a6; color:#fff; border:none; padding:8px 22px;
              border-radius:4px; cursor:pointer; font-size:14px; }
 .btn-greet.done { background:#ddd; color:#888; }
 .pagination { margin-top:16px; text-align:center; }
 a[href*='/job_detail/'] { text-decoration:none; color:inherit; }
"""

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{title}</title><style>{css}</style></head>
<body>
  <div class="header-v2">
    <span class="logo">BOSS直聘</span>
    <span class="nav-figure"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="avatar"></span>
  </div>

  <div class="page-jobs-main">
    <div class="job-search-form">共 {n} 个职位</div>
    <ul class="recommend-result-job">
      {items}
    </ul>
    <div class="pagination">{pager}</div>
  </div>
</body>
</html>
"""

_ITEM = """      <li class="card-inner">
        <a href="/job_detail/{jid}.html" target="_blank">
          <span class="job-name">{title}</span>
          <span class="salary">{salary}</span>
          <span class="job-area">{city}·{district}</span>
        </a>
        <div class="company-name">{company}</div>
        <ul class="tag-list">{tags}</ul>
      </li>
"""

# 详情页：带"立即沟通"按钮，点击后变成"继续沟通"
_DETAIL = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{title} - BOSS直聘</title><style>{css}</style></head>
<body>
  <div class="header-v2">
    <span class="logo">BOSS直聘</span>
    <span class="nav-figure"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="avatar"></span>
  </div>
  <div class="page-jobs-main">
    <h1 class="job-name">{title}</h1>
    <div class="salary">{salary}</div>
    <div class="job-area">{city}·{district}</div>
    <div class="company-name">{company}</div>
    <div class="detail-op">
      <button class="btn-greet" id="greet" onclick="greet()">立即沟通</button>
    </div>
  </div>
<script>
  function greet() {{
    var b = document.getElementById('greet');
    if (b.classList.contains('done')) return;
    b.classList.add('done');
    b.innerText = '继续沟通';
  }}
</script>
</body>
</html>
"""


def _tags(items: list[str]) -> str:
    return "".join(f"<li>{t}</li>" for t in items)


# 故意混入各种会被规则拦掉 / 放行的样本
_SAMPLES = [
    ("j001", "Java开发工程师", "星辰科技", "25-40K", "深圳", "南山区",
     ["五险一金", "年终奖"]),
    ("j002", "Java外包岗", "某某人力资源", "18-25K", "深圳", "福田区",
     ["外包", "驻场"]),
    ("j003", "后端开发工程师", "云图网络", "30-50K", "深圳", "科技园",
     ["弹性工作", "股票期权"]),
    ("j004", "前端开发工程师", "某互联网", "25-45K", "深圳", "南山区",
     ["大厂", "双休"]),
    ("j005", "Golang开发工程师", "海纳数据", "20-35K", "深圳", "宝安区",
     ["扁平管理"]),
    ("j006", "Java开发工程师", "小作坊科技", "12-18K", "深圳", "龙岗区",
     ["创业公司"]),
    ("j007", "后端开发工程师", "远方科技", "28-45K", "北京", "海淀区",
     ["上市公司"]),
    ("j008", "Java开发工程师", "全球外派服务", "22-30K", "深圳", "南山区",
     ["外派"]),
    ("j009", "高级Java工程师", "蓝海科技", "面议", "深圳", "南山区",
     ["五险一金"]),
    ("j010", "Java开发工程师", "星辰科技", "25-40K", "深圳", "南山区",
     ["招聘中"]),
]


def build_search_page(sample_ids: list[str] | None = None) -> str:
    items, n = "", 0
    for row in _SAMPLES:
        jid, title, company, salary, city, district, tags = row
        if sample_ids and jid not in sample_ids:
            continue
        items += _ITEM.format(jid=jid, title=title, company=company,
                              salary=salary, city=city, district=district,
                              tags=_tags(tags))
        n += 1
    return _PAGE.format(title="职位搜索 - BOSS直聘", css=_CSS, items=items,
                        n=n, pager='<span class="cur">1</span>')


def build_detail_page(job_id: str) -> str:
    for row in _SAMPLES:
        if row[0] == job_id:
            _, title, company, salary, city, district, _tags = row
            return _DETAIL.format(title=title, css=_CSS, company=company,
                                  salary=salary, city=city, district=district)
    raise KeyError(job_id)


def write_mock_site(out_dir: str | Path) -> Path:
    """把假站点写到磁盘，返回入口文件路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    index = out / "jobs.html"
    index.write_text(build_search_page(), encoding="utf-8")

    for row in _SAMPLES:
        p = out / f"job_detail_{row[0]}.html"
        p.write_text(build_detail_page(row[0]), encoding="utf-8")

    # 真实 URL 形态是 /job_detail/xxx.html，本地用扁平文件名近似，
    # 再额外产出一个"空结果"页用于验证登录墙场景
    (out / "empty.html").write_text(
        _PAGE.format(title="职位 - BOSS直聘", css=_CSS, items="", n=0,
                     pager="").replace(
            '<div class="job-search-form">共 0 个职位</div>',
            '<div class="job-empty-wrapper"><div class="job-empty-box">'
            '查看更多职位请登录查看全部职位</div></div>'),
        encoding="utf-8")

    return index
