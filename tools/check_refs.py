"""静态检查：确认跨模块引用的名字真实存在。

本地装不上 playwright，无法做真实浏览器冒烟，
这个脚本至少能挡住"引用了不存在的类/函数"这类低级错误。
"""

import sys
from pathlib import Path

# 让脚本可以直接 `python tools/xxx.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "cdp"))

import ast
import pathlib

SRC = pathlib.Path(__file__).parent.parent / "src" / "boss"


def collect_top_level(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def main() -> int:
    exports = {p.stem: collect_top_level(p) for p in SRC.rglob("*.py")}

    problems: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # from .xxx import a, b
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                target = exports.get(node.module)
                if target is None:
                    problems.append(f"{path.stem}: 找不到模块 .{node.module}")
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if alias.name not in target:
                        problems.append(
                            f"{path.stem}: .{node.module} 中没有 {alias.name}")
            # from src.boss.xxx import a
            elif isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if len(parts) > 2 and parts[-1] in exports:
                    for alias in node.names:
                        if alias.name not in exports[parts[-1]]:
                            problems.append(
                                f"{path.stem}: {node.module} 中没有 {alias.name}")

    if problems:
        print("发现问题：")
        for p in problems:
            print("  -", p)
        return 1
    print("[ok] 跨模块引用检查通过，未发现引用不存在的名字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
