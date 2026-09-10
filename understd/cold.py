#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cold_read.py · 冷读者闸门

判"懂没懂"。脚本判不了的那三条（U0 落地句、U5 紧身衣、U6 复述），交给它。

原理：另起一个没有上下文的会话，只给正文，问它读到了什么。
再把它的复述，与你声明的落地句对照。对不上就退回。

为什么要跑多次：2026-09-10 实测，同一份稿两次判定结果相反。
单次判定有噪声，所以默认跑三次取多数。

用法：
    python3 understd/cold.py 交付稿.md                 # 严格档，交付物用
    python3 understd/cold.py 回复.md --lenient          # 对话档，卡词只提示
    python3 understd/cold.py 稿.md --runs 5 --json
    python3 understd/cold.py 稿.md --intent "一句话"    # 不写标记时用参数传

落地句写在正文首行即可：`【落地句】一句话`

退出码：放行为 0，退回为 1，工具故障为 2。

成本：每次冷读加判定约 5 秒，跑三次约 15 秒（2026-09-10 本机实测）。
只对交付物和长回复开闸。日常寒暄别开。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import strip_markup  # noqa: E402

# 读稿的智能体命令。默认自动探测，也可以用 --reader-cmd 或环境变量指定。
READER_ENV = "UNDERSTD_READER_CMD"
READER_CANDIDATES = (
    ("dsh", ["dsh", "--profile", "headless"]),
    ("claude", ["claude", "-p"]),
    ("codex", ["codex", "exec"]),
)
COLD_DIR = os.path.join(tempfile.gettempdir(), "understd-cold-reader")
MARK = re.compile(r"^【落地句】\s*(.+)$", re.M)
NONE_WORDS = ("无", "", "None", "none", "没有")

COLD_PROMPT = """你是冷读者。你没有背景资料，只能读到下面这段文字。
只回答三件事，用一行 JSON 输出。禁止使用任何工具，禁止写文件，禁止解释。
{{"复述":"用一句话说这段文字讲了什么","最该记住":"照抄你最可能记住的那句原句","卡住":"哪一句或哪个词让你读不懂，没有就填无"}}

文字如下：
{body}"""

JUDGE_PROMPT = """只做一件事：判断读者有没有抓住作者的落点。
甲是作者声明的一句话主张。乙是读者读完全文后的复述。
乙比甲多出细节，不算错——文章本来就比一句话长。
只有两种情况算错：乙丢了甲的主张，或把甲的主张说成了别的主张。
用一行 JSON 输出，不要解释。
{{"一致":"是或否","差在哪":"不一致时说清错在哪，一致时填无"}}

甲（作者的落点）：{intent}
乙（读者的复述）：{recite}"""


def resolve_reader(explicit=None):
    """定下用哪个命令当冷读者：参数 > 环境变量 > 自动探测。"""
    if explicit:
        return explicit.split()
    env = os.environ.get(READER_ENV)
    if env:
        return env.split()
    for name, argv in READER_CANDIDATES:
        if shutil.which(name):
            return argv
    return None


def run_cold(prompt: str, reader, timeout: int = 180) -> str:
    if not reader:
        raise RuntimeError(
            "找不到可用的读稿命令。请用 --reader-cmd 指定，"
            f"或设置环境变量 {READER_ENV}，例如：--reader-cmd 'dsh --profile headless'")
    Path(COLD_DIR).mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [*reader, prompt],
        cwd=COLD_DIR, capture_output=True, text=True, timeout=timeout)
    return (proc.stdout or "").strip()


def parse_json(text: str):
    """从模型输出里挖出那一行 JSON。挖不到返回 None。"""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def is_stuck(value) -> bool:
    return str(value).strip() not in NONE_WORDS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="冷读者闸门")
    ap.add_argument("file", help="待读的草稿")
    ap.add_argument("--intent", help="落地句；不传则从正文里的【落地句】行取")
    ap.add_argument("--runs", type=int, default=3, help="跑几次取多数，默认 3")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-judge", action="store_true", help="只冷读，不比对落地句")
    ap.add_argument("--reader-cmd", help="读稿命令，例如 \"dsh --profile headless\"")
    ap.add_argument("--lenient", action="store_true",
                    help="对话档：卡住的词只提示，只有复述对不上才退回")
    args = ap.parse_args(argv)

    p = Path(args.file)
    if not p.exists():
        print(f"找不到文件：{p}", file=sys.stderr)
        return 2
    raw = p.read_text(encoding="utf-8")
    intent = args.intent
    if not intent:
        m = MARK.search(raw)
        intent = m.group(1).strip() if m else None
    body = MARK.sub("", strip_markup(raw)).strip()   # 落地句不能漏给冷读者
    if not body:
        print("正文是空的，没什么可读。", file=sys.stderr)
        return 2

    reader = resolve_reader(args.reader_cmd)
    if not reader:
        print("没有可用的读稿命令。用 --reader-cmd 指定，或设 UNDERSTD_READER_CMD。", file=sys.stderr)
        return 2
    runs, agreed, stuck_votes = [], [], 0
    for i in range(max(1, args.runs)):
        cold = parse_json(run_cold(COLD_PROMPT.format(body=body[:12000]), reader))
        if not cold:
            print(f"第 {i + 1} 次冷读没给出可解析答复，跳过。", file=sys.stderr)
            continue
        recite = cold.get("复述", "")
        stuck = cold.get("卡住", "无")
        if is_stuck(stuck):
            stuck_votes += 1
        verdict, gap = None, "无"
        if intent and not args.no_judge:
            judge = parse_json(run_cold(JUDGE_PROMPT.format(intent=intent, recite=recite), reader))
            if judge:
                verdict = judge.get("一致", "")
                gap = judge.get("差在哪", "无")
            if verdict:
                agreed.append(verdict)
        runs.append({"recite": recite, "most": cold.get("最该记住", ""),
                     "stuck": stuck, "verdict": verdict, "gap": gap})

    if not runs:
        print("冷读者一次都没跑通。", file=sys.stderr)
        return 2

    n = len(runs)
    stuck_majority = stuck_votes * 2 > n
    yes = sum(1 for v in agreed if v == "是")
    no = sum(1 for v in agreed if v == "否")
    if not agreed:
        verdict, gap = None, "无"
    elif yes == no:
        verdict, gap = "不稳定", "两次判定相反，请人看一眼"
    elif yes > no:
        verdict, gap = "是", "无"
    else:
        verdict = "否"
        gap = next((r["gap"] for r in runs
                    if r["verdict"] == "否" and r["gap"] not in ("无", "", None)), "无")

    blocked = (verdict == "否") or (stuck_majority and not args.lenient)
    if args.json:
        print(json.dumps({
            "file": str(p), "intent": intent, "runs": n,
            "recites": [r["recite"] for r in runs],
            "most_remembered": [r["most"] for r in runs],
            "stuck": [r["stuck"] for r in runs],
            "consistent": verdict, "gap": gap,
            "verdict": "退回" if blocked else ("放行·有卡词" if stuck_majority else "放行"),
        }, ensure_ascii=False, indent=2))
    else:
        print(f"冷读者 · {p.name} · 跑 {n} 次")
        print("")
        print(f"它读到的：{runs[0]['recite']}")
        for i, r in enumerate(runs[1:], 2):
            print(f"第 {i} 次：{r['recite'][:50]}")
        if intent:
            print(f"你想说的：{intent}")
            print(f"是否一致：{verdict}（{yes} 是 / {no} 否）")
            if gap not in ("无", "", None):
                print(f"差在哪：{gap}")
        print(f"卡住的地方：{runs[0]['stuck']}")
        for i, r in enumerate(runs[1:], 2):
            if is_stuck(r["stuck"]):
                print(f"第 {i} 次也卡：{r['stuck'][:50]}")
        print("")
        if blocked:
            print("结论：退回，先改再发。")
        elif stuck_majority:
            print("结论：放行（对话档）。卡住的词记着，交付时要补。")
        else:
            print("结论：放行。它读懂了。")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
