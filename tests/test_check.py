#!/usr/bin/env python3
"""检查器的基础测试。不装任何依赖：python3 tests/test_check.py"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "understd" / "check.py"


def run(*args):
    return subprocess.run([sys.executable, str(CHECK), *args],
                          capture_output=True, text=True)


def main() -> int:
    failed = 0

    r = run("--self-test")
    ok = r.returncode == 0 and "7/7" in r.stdout
    print(f"{'通过' if ok else '失败'} · 内置自检 7/7")
    failed += 0 if ok else 1

    bad = ROOT / "tests" / "_tmp_bad.md"
    bad.write_text("读者读完全文，脑中若没有一个词被重新称过——这篇文，等于没写。\n", encoding="utf-8")
    r = run(str(bad))
    ok = r.returncode == 1 and "U1" in r.stdout
    print(f"{'通过' if ok else '失败'} · 长句被抓且退出码为 1")
    failed += 0 if ok else 1

    good = ROOT / "tests" / "_tmp_good.md"
    good.write_text("先定落地句。就是一句话，读完能复述。上次写卡片时，我漏了这句。\n", encoding="utf-8")
    r = run(str(good))
    ok = r.returncode == 0
    print(f"{'通过' if ok else '失败'} · 干净文本退出码为 0")
    failed += 0 if ok else 1

    bad.unlink(missing_ok=True)
    good.unlink(missing_ok=True)
    print(f"\n共 {3 - failed}/3 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
