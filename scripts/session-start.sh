#!/usr/bin/env bash
# SessionStart hook: セッション冒頭に記憶の鮮度と直近の変更を Claude に見せる。
# 標準出力がそのままコンテキストに入る。失敗しても何も出さずに終わる(セッションを止めない)。
set -u
cd "$(dirname "$0")/.." 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

echo "## ai-memory: セッション開始時の状態"
echo
echo "### 直近の記憶更新(git log)"
git log --date=short --pretty='- %ad %s' -8 2>/dev/null
echo
last=$(grep -m1 -o '最終更新: [0-9-]*' current.md 2>/dev/null | sed 's/最終更新: //')
if [ -n "${last:-}" ]; then
  now=$(date +%s); then_=$(date -j -f %Y-%m-%d "$last" +%s 2>/dev/null || date -d "$last" +%s 2>/dev/null || echo "$now")
  days=$(( (now - then_) / 86400 ))
  echo "### current.md の最終更新: $last($days 日前)"
  [ "$days" -ge 14 ] && echo "- 2週間以上更新されていない。古い前提で答えないよう、必要なら本人に現状を確認する"
else
  echo "### current.md の最終更新日が見つからない(冒頭に「最終更新: YYYY-MM-DD」を書く)"
fi
echo
st=$(git status --porcelain --untracked-files=all 2>/dev/null)
if [ -n "$st" ]; then
  echo "### 未コミットの変更がある(前回セッションの学習が途中の可能性)"
  echo "$st" | sed 's/^/- /'
fi
ahead=$(git rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)
[ "${ahead:-0}" -gt 0 ] && echo "### 未プッシュのコミットが $ahead 件ある。作業前に push する"
exit 0
