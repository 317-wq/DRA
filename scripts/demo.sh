#!/usr/bin/env bash
# 一键跑三个不同形态的演示主题，结束后列出全部 run。
set -euo pipefail
cd "$(dirname "$0")/.."
UV="${UV:-$HOME/.local/bin/uv}"

topics=(
  "2026 年主流 Agent 记忆机制方案对比"
  "2026 年 RAG 与长上下文方案的选择边界"
  "2026 年中国新能源汽车换电模式的现状与争议"
)

for t in "${topics[@]}"; do
  echo "==> $t"
  "$UV" run dra run "$t" --max-sources 8
done

"$UV" run dra list
