#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""understd_check.py · 理解优先检查器

规则说明见 docs/规则说明.md（U 系列编号）。

用法：
    python3 understd/check.py 草稿.md
    python3 understd/check.py 草稿.md --json
    python3 understd/check.py 草稿.md --candidate      # 连候选规则 U7/U8 一起查
    python3 understd/check.py --self-test              # 跑内置回归样例

退出码：有硬违规为 1，干净为 0。可直接做循环闸门。

边界：脚本只判"可数件"。U0 落地句、U5 紧身衣、U6 复述，它判不了，
会在报告末尾列为"需人判"，不假装通过。
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

CJK = "\u4e00-\u9fff"
CJK_RE = re.compile(f"[{CJK}]")

LONG = 25          # U1 单句上限
SHORT = 10         # U1b 断句下限
TIRED_RUN = 5      # U1b 连续几句算电报体
HEAD_CHARS = 45    # U2 看后面多少个字里有人话
CAP = 8            # 同类提示最多列几条

SENT_END = "。！？；!?;"
QUOTE_OPEN = "「『“‘\""
QUOTE_CLOSE = "」』”’\""
PUNCT = set("，。！？；：、（）「」『』《》“”‘’…—·,.!?;:()[]{}<>\"'/\\|~#$%&^@`+*=_-")

TRANSLATION_MARK = re.compile(
    r"就是|即|意思|也就是|换句话说|也就是说|指的是|译为|俗称|好比|例如|比如|等于|所谓")
CONCRETE = re.compile(r"\d|比如|例如|举个|就像|好比|那天|上周|一次|一家|一个叫|场景|案例|故事|[「『]")
REASON_CONN = re.compile(r"因此|所以|于是|由此可见|这意味着|进而|从而|因为|故而")
TERM_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]{2,}")
NUM_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|倍|万|亿)")
JUDGE = re.compile(r"是|就是|意味着|说明|证明|本质|关键|核心|必须|应该|应当|只有|才能|决定|构成")
ABSTRACT = re.compile(
    r"理解|认知|机制|价值|结构|意义|本质|范式|方法论|逻辑|原则|系统|框架|概念|定义"
    r"|思维|模式|关系|层次|维度|路径|能力|信任|效率|冲突|边界|惯性")
BANNED = re.compile(r"然而|值得注意的是|综上所述|赋能|抓手|本质上")
# 作者自己标成术语的地方：加粗、<b>、引号
MARKED = re.compile(
    r"\*\*([\u4e00-\u9fff]{2,4})\*\*"
    r"|<b>([\u4e00-\u9fff]{2,4})</b>"
    r"|<strong>([\u4e00-\u9fff]{2,4})</strong>"
    r"|[「『“]([\u4e00-\u9fff]{2,4})[」』”]")


# ---------------------------------------------------------------- 文本预处理

def strip_markup(raw: str) -> str:
    t = raw
    t = re.sub(r"```.*?```", "\n", t, flags=re.S)
    t = re.sub(r"<pre.*?</pre>", "\n", t, flags=re.S | re.I)   # 引用块：给人照抄的原文，不按我的句子标准判
    t = re.sub(r"~~~.*?~~~", "\n", t, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<script.*?</script>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(p|div|li|h[1-6]|tr|td)\s*>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"`[^`\n]*`", " ", t)
    return t


FURNITURE = re.compile(
    r"<h[1-6][^>]*>.*?</h[1-6]>"
    r'|<(?:div|p)[^>]*class="[^"]*\b(?:mast|promise)\b[^"]*"[^>]*>.*?</(?:div|p)>',
    re.S | re.I)


def strip_furniture(raw: str) -> str:
    """去掉刊头与标题。它们是装帧，不是句子，不该按句长算。"""
    return FURNITURE.sub("\n", raw)


def split_blocks(text: str):
    """切成块，并标出类型：text / list / table / heading。"""
    blocks = []
    for chunk in re.split(r"\n\s*\n", text):
        lines = [ln.rstrip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        if all(ln.lstrip().startswith("#") for ln in lines):
            kind = "heading"
        elif all(ln.lstrip().startswith(">") for ln in lines):
            kind = "meta"
        elif all(ln.lstrip().startswith("【落地句】") for ln in lines):
            kind = "meta"
        elif all(ln.lstrip().startswith("|") for ln in lines):
            kind = "table"
        elif all(re.match(r"\s*(?:[-*+]|\d+[.、)])\s", ln) for ln in lines):
            kind = "list"
        else:
            kind = "text"
        blocks.append({"kind": kind, "lines": lines})
    return blocks


def split_sentences(line: str):
    out, buf = [], ""
    for ch in line:
        buf += ch
        if ch in SENT_END:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def zh_len(s: str) -> int:
    s = re.sub(r"\s+", "", s)
    s = "".join(ch for ch in s if ch not in PUNCT)
    zh = len(CJK_RE.findall(s))
    rest = len(re.sub(f"[{CJK}]", "", s))
    return zh + (rest + 1) // 2


def is_pure_quote(s: str) -> bool:
    return len(s) > 3 and s[0] in QUOTE_OPEN and s[-1] in QUOTE_CLOSE


def build_sentences(blocks):
    """扁平化成句表：每句带段落号、段内序号、字数。"""
    sents = []
    for pi, b in enumerate(blocks, 1):
        if b["kind"] != "text":
            continue
        idx = 0
        for ln in b["lines"]:
            for s in split_sentences(ln):
                idx += 1
                sents.append({
                    "text": s, "len": zh_len(s), "para": pi,
                    "sidx": idx, "kind": b["kind"],
                })
    return sents


# ---------------------------------------------------------------- 规则判定

SKIP_TOKEN = re.compile(r"[._/\\]")


def find_terms(s: str):
    terms = [m.group(0) for m in TERM_LATIN.finditer(s)
             if not SKIP_TOKEN.search(m.group(0))]
    terms += [m.group(0) for m in NUM_UNIT.finditer(s)]
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def marked_terms(raw: str):
    """找出作者自己标成术语的词：加粗、<b>、引号包住的 2-4 字。"""
    counts = {}
    for m in MARKED.finditer(raw):
        term = next(g for g in m.groups() if g)
        counts[term] = counts.get(term, 0) + raw.count(term)
    return counts


def check(blocks, sents, raw="", candidate=False, norm_mode=False):
    hard, soft, cand = [], [], []
    by_para = {}
    for s in sents:
        by_para.setdefault(s["para"], []).append(s)

    # U1 长句
    over = 0
    for s in sents:
        if s["len"] > LONG and not is_pure_quote(s["text"]):
            over += 1
            item = {
                "rule": "U1", "where": f"第 {s['para']} 段第 {s['sidx']} 句",
                "text": s["text"], "len": s["len"],
                "fix": f"断成短句：逗号、破折号、顿号处都能断。现在 {s['len']} 字。",
            }
            (soft if norm_mode else hard).append(item)
    if over:
        for it in (soft if norm_mode else hard):
            if it["rule"] == "U1":
                it["_n"] = over

    # U1b 电报体：连着几句都太短
    run = []
    for s in sents:
        if s["len"] < SHORT:
            run.append(s)
            continue
        if len(run) >= TIRED_RUN:
            hard.append({
                "rule": "U1b", "where": f"第 {run[0]['para']} 段第 {run[0]['sidx']} 句起",
                "text": "／".join(x["text"] for x in run[:4]) + "……",
                "fix": f"连着 {len(run)} 句都不到 {SHORT} 字，读起来像电报。挑两处合成一句。",
            })
        run = []
    if len(run) >= TIRED_RUN:
        hard.append({
            "rule": "U1b", "where": f"第 {run[0]['para']} 段第 {run[0]['sidx']} 句起",
            "text": "／".join(x["text"] for x in run[:4]) + "……",
            "fix": f"连着 {len(run)} 句都不到 {SHORT} 字，读起来像电报。挑两处合成一句。",
        })

    # U2 术语/数据没翻译
    hit_terms = {}
    for s in sents:
        follow = " ".join(x["text"] for x in by_para[s["para"]])
        for t in find_terms(s["text"]):
            pos = s["text"].find(t)
            window = (s["text"] + follow)[pos:pos + len(t) + HEAD_CHARS]
            if TRANSLATION_MARK.search(window):
                continue
            key = (s["para"], t)
            if key in hit_terms:
                continue
            hit_terms[key] = True
            soft.append({
                "rule": "U2", "where": f"第 {s['para']} 段第 {s['sidx']} 句",
                "text": f"「{t}」{s['text']}",
                "fix": f"紧跟半句人话，例如「{t}，就是……」；译完再往下走。",
            })

    # U2c 自己标成术语的词，第一次出现没解释
    for term, times in marked_terms(raw).items():
        if times < 2:
            continue
        first = next((s for s in sents if term in s["text"]), None)
        if not first:
            continue
        follow = " ".join(x["text"] for x in by_para[first["para"]])
        pos = first["text"].find(term)
        window = (first["text"] + follow)[pos:pos + len(term) + HEAD_CHARS]
        if TRANSLATION_MARK.search(window):
            continue
        soft.append({
            "rule": "U2c", "where": f"第 {first['para']} 段第 {first['sidx']} 句",
            "text": f"「{term}」出现 {times} 次，首次：{first['text'][:40]}",
            "fix": f"它被你标成了术语。第一次出现就补半句人话：「{term}，就是……」。",
        })

    # U3 抽象结论后面没场景
    for pi, group in by_para.items():
        for s in group:
            if s["len"] < 12 or not (JUDGE.search(s["text"]) and ABSTRACT.search(s["text"])):
                continue
            after = [x["text"] for x in group if x["sidx"] > s["sidx"]][:2]
            if any(CONCRETE.search(x) for x in after):
                continue
            soft.append({
                "rule": "U3", "where": f"第 {pi} 段第 {s['sidx']} 句",
                "text": s["text"],
                "fix": "后面补一个能看见的东西：人名、时间、数字，或一句「比如」。",
            })

    # U4 连续推理没有着陆句
    run = []
    for s in sents:
        if REASON_CONN.search(s["text"]):
            run.append(s)
            continue
        if len(run) >= 3 and not any(CONCRETE.search(x["text"]) for x in run):
            soft.append({
                "rule": "U4", "where": f"第 {run[0]['para']} 段第 {run[0]['sidx']} 句起",
                "text": "／".join(x["text"] for x in run[:3]) + "……",
                "fix": f"{len(run)} 句连着推理，中间没有喘气。插一句结论或一个实例。",
            })
        run = []

    # U7 禁词、U8 段落句数（候选规则，默认不查）
    if candidate:
        for s in sents:
            for m in BANNED.finditer(s["text"]):
                cand.append({
                    "rule": "U7", "where": f"第 {s['para']} 段第 {s['sidx']} 句",
                    "text": s["text"], "fix": f"删掉「{m.group(0)}」，读一遍看信息少没少。",
                })
        for pi, group in by_para.items():
            if len(group) > 3:
                cand.append({
                    "rule": "U8", "where": f"第 {pi} 段",
                    "text": "／".join(x["text"] for x in group)[:60] + "……",
                    "fix": f"这段 {len(group)} 句，超了三句。从话题转弯处切开。",
                })

    for lst in (hard, soft, cand):
        for it in lst:
            it.pop("_n", None)
    return hard, soft, cand


# ---------------------------------------------------------------- 报告

def render(path, blocks, sents, hard, soft, cand, norm_mode):
    chars = sum(s["len"] for s in sents)
    bodies = [b for b in blocks if b["kind"] == "text"]
    skip = {}
    for b in blocks:
        if b["kind"] != "text":
            skip[b["kind"]] = skip.get(b["kind"], 0) + 1
    label = {"heading": "标题", "meta": "引用", "table": "表格",
             "list": "列表", "code": "代码"}
    out = []
    out.append(f"理解检查 · {path.name}")
    out.append(f"规模：正文 {len(bodies)} 块 / {len(sents)} 句 / 约 {chars} 字")
    if skip:
        brief = "、".join(f"{label.get(k, k)} {v}" for k, v in skip.items())
        out.append(f"按豁免跳过：{brief}（宪法规定列表、表格、代码块不受字数硬卡）")
    if norm_mode:
        out.append("注：这是规范文件，U1 长句降为提示，不算硬违规。")
    out.append("")

    def dump(title, items):
        if not items:
            return
        out.append(f"{title}（{len(items)} 处）")
        for it in items[:CAP]:
            out.append(f"[{it['rule']}] {it['where']}")
            out.append(f"    原句：{it['text'][:70]}")
            out.append(f"    改法：{it['fix']}")
        if len(items) > CAP:
            out.append(f"    （同类还有 {len(items) - CAP} 处，未列）")
        out.append("")

    dump("硬违规 · 必须改", hard)
    dump("提示 · 需人确认", soft)
    dump("候选规则命中 · 未生效", cand)

    out.append("需人判（脚本判不了）")
    out.append("U0 落地句：另起冷读者，只给正文，问「这篇想说什么」。")
    out.append("U5 紧身衣：逐句删修饰语，看语义是否变。")
    out.append("U6 复述：最该记住的三句，是否醒目。")
    out.append("")
    if hard:
        out.append(f"结论：{len(hard)} 处硬违规，改到零再发。")
    else:
        out.append("结论：没有硬违规。常识那关请人过。")
    return "\n".join(out)


# ---------------------------------------------------------------- 自检

SELF_TEST = [
    ("长句该被抓",
     "辩手在时限里称词——把关键词身上挂着的几把秤逐一掂过，选一把，让整场辩论为它作证。",
     "U1", "hard"),
    ("术语没翻译该被抓", "我们用 agent 来做这件事。", "U2", "soft"),
    ("术语有翻译不该抓", "我们用 agent，也就是干活的程序，来做这件事。", "U2", "none"),
    ("抽象结论没场景该被抓", "这套方法的关键在于理解机制。", "U3", "soft"),
    ("引用块里的长句不算我的句子",
     '<p>下面这段给用户照抄。</p><pre>请把仓库放到本机目录，然后按说明装好依赖并跑通自带测试，最后把结果贴给我看。</pre>',
     "U1", "none"),
    ("卡片刊头不算正文",
     '<div class="mast">交付 · 核验</div>\n<h1>干净落地</h1>\n<div class="jud"><p>这次重启一次到位。我在新进程里。</p></div>'
     '<p class="ln">新的服务进程号是 36090，启动于十四点零五分零八秒，端口也回了正常码。</p>',
     "U1b", "none"),
    ("干净文本不该抓",
     "先定落地句。就是一句话，读完能复述。上次写卡片时，我漏了这句。",
     "U1", "none"),
]


def self_test() -> int:
    bad = 0
    for name, text, rule, want in SELF_TEST:
        blocks = split_blocks(strip_markup(text))
        sents = build_sentences(blocks)
        hard, soft, cand = check(blocks, sents, raw=text, candidate=True)
        got = "hard" if any(x["rule"] == rule for x in hard) else (
            "soft" if any(x["rule"] == rule for x in soft) else (
                "cand" if any(x["rule"] == rule for x in cand) else "none"))
        ok = got == want
        bad += 0 if ok else 1
        print(f"{'通过' if ok else '失败'} · {name} · 期望 {want}，实得 {got}")
    print(f"\n自检：{len(SELF_TEST) - bad}/{len(SELF_TEST)} 通过")
    return 1 if bad else 0


# ---------------------------------------------------------------- 入口

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="理解优先检查器（输出宪法 U 系列）")
    ap.add_argument("file", nargs="?", help="待查文本文件；省略则读标准输入")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--candidate", action="store_true", help="连候选规则 U7/U8 一起查")
    ap.add_argument("--norm", action="store_true", help="规范/笔记模式：U1 长句只提示，不当硬违规")
    ap.add_argument("--self-test", action="store_true", help="跑内置回归样例")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"找不到文件：{p}", file=sys.stderr)
            return 2
        raw = p.read_text(encoding="utf-8")
    else:
        p = Path("-")
        raw = sys.stdin.read()

    blocks = split_blocks(strip_markup(strip_furniture(raw)))
    sents = build_sentences(blocks)
    norm_mode = args.norm or "协议" in p.parts or "输出宪法" in p.name
    hard, soft, cand = check(blocks, sents, raw, candidate=args.candidate, norm_mode=norm_mode)

    if args.json:
        print(json.dumps({
            "file": str(p), "blocks": len(blocks), "sentences": len(sents),
            "hard": hard, "soft": soft, "candidate": cand,
        }, ensure_ascii=False, indent=2))
    else:
        print(render(p, blocks, sents, hard, soft, cand, norm_mode))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
