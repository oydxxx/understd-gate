#!/bin/bash
# understd-gate 安装脚本
#
# 做三件事：
#   1. 把工具装到 ~/.understd
#   2. 跑一次自带自检，确认能跑
#   3. 打印怎么用
#
# 用法：bash install.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${UNDERSTD_HOME:-$HOME/.understd}"

echo "==> 安装到 $DEST"
mkdir -p "$DEST"
cp -R "$SRC/understd/." "$DEST/"
mkdir -p "$DEST/docs" && cp -R "$SRC/docs/." "$DEST/docs/" 2>/dev/null || true

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "!! 没找到 python3。请先装 Python 3（macOS 可执行：brew install python3）"
  exit 1
fi
echo "==> 使用 Python：$PY"

echo "==> 跑自检"
"$PY" "$DEST/check.py" --self-test

cat <<EOF

装好了。以后这样用：

  检查一份稿子：   python3 "$DEST/check.py" 稿子.md
  连规范文档也查： python3 "$DEST/check.py" 规范.md --norm
  判读者懂不懂：   python3 "$DEST/cold.py" 稿子.md --runs 3

想让它自动跑，就在你的智能体规则文件里加一行：

  交付前先跑：python3 "$DEST/check.py" 草稿.md

冷读者需要一个能读稿的智能体命令。默认自动探测 dsh / claude / codex，
也可以指定：python3 "$DEST/cold.py" 稿子.md --reader-cmd "claude -p"
EOF
