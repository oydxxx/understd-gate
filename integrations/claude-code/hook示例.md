# Claude Code 接法

两条路，任选。

## 一、只加规则（最省事）

把 `../agents-md/AGENTS.md片段.md` 里的那段贴进项目的 `CLAUDE.md`。
效果：模型自己会记得跑，但它也可能忘——提示是概率，不是开关。

## 二、加钩子（能强制）

在 `.claude/settings.json` 里挂一个写文件后触发的钩子，
对 `.md` 文件自动跑检查器，把违规打回给模型。

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.understd/check.py \"$CLAUDE_FILE_PATH\" || true"
          }
        ]
      }
    ]
  }
}
```

注意两点：

- 钩子里加 `|| true`，否则检查失败会打断整个流程。
- 真正拦住它的做法是让钩子返回非零并输出违规清单。各家实现不同，按你的版本调。
