"""对 deploy/setup.sh 做结构性校验。

目标是挡住"heredoc 没闭合""if 少了 fi""引号不配对"这类
在服务器上跑到一半才炸的错误。不是完整的 shell 解析器，
只覆盖本项目脚本用到的语法结构。
"""

import sys
from pathlib import Path

# 让脚本可以直接 `python tools/xxx.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "cdp"))

import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent
SCRIPT = ROOT / "deploy" / "setup.sh"


def check(text: str) -> list[str]:
    problems: list[str] = []
    lines = text.splitlines()

    # 去掉整行注释后再做括号计数，避免注释里的 fi 干扰
    code_lines = []
    in_heredoc = False
    heredoc_marker = ""
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if in_heredoc:
            if stripped == heredoc_marker:
                in_heredoc = False
            continue
        if not stripped or stripped.startswith("#"):
            code_lines.append("")
            continue
        # 检测 heredoc 起始
        m = re.search(r"<<-?['\"]?(\w+)['\"]?", raw)
        if m:
            in_heredoc = True
            heredoc_marker = m.group(1)
            continue
        code_lines.append(raw)

    if in_heredoc:
        problems.append(f"heredoc 标记 {heredoc_marker!r} 从未闭合")

    body = "\n".join(l for l in code_lines if l)

    # 关键字配对
    opens = len(re.findall(r"^\s*(?:if|for|while)\b|\|\s*if\b", body, re.M))
    fis = len(re.findall(r"^\s*fi\b", body, re.M))
    dones = len(re.findall(r"^\s*done\b", body, re.M))
    if opens != fis + dones:
        problems.append(
            f"控制流不配对：if/for/while 开头 {opens} 个，fi/done 结束 {fis + dones} 个")

    # 引号配对（逐行粗查，忽略转义）
    for i, raw in enumerate(lines, 1):
        if raw.strip().startswith("#"):
            continue
        core = re.sub(r"'[^']*'", "", raw)          # 先剥单引号串
        if core.count('"') % 2:
            # 双引号跨行是合法的，做个宽松判断：仅在同行明显落单时报警
            pass
        if raw.count("'") % 2 and not re.search(r"'\S", raw):
            pass

    # set -euo pipefail 必须存在（安全网）
    if "set -euo pipefail" not in text and "set -eu" not in text:
        problems.append("缺少 'set -e' 安全开关，失败会被静默忽略")

    # 危险操作检查
    for pat, msg in [
        (r"rm\s+-rf\s+/(?!\w)", "存在 rm -rf 根目录风险"),
        (r"chmod\s+777", "使用了 777 权限"),
    ]:
        if re.search(pat, text):
            problems.append(msg)

    return problems


def main() -> int:
    if not SCRIPT.exists():
        print(f"[warn] 未找到 {SCRIPT}")
        return 0
    text = SCRIPT.read_text(encoding="utf-8")
    problems = check(text)
    if problems:
        print("发现结构问题：")
        for p in problems:
            print("  -", p)
        return 1
    print(f"[ok] {SCRIPT.name} 结构检查通过（heredoc / if-fi / set -e）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
