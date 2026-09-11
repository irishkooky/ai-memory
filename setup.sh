#!/usr/bin/env bash
# ai-memory の初期化。テンプレートから作った直後に1回だけ実行する。
set -eu
cd "$(dirname "$0")"

read -r -p "所有者名(人名または会社名。AGENTS.md に入ります): " owner
[ -n "$owner" ] || { echo "所有者名は必須です"; exit 1; }

python3 - "$owner" <<'PY'
import sys, re, datetime, pathlib
owner = sys.argv[1]
today = datetime.date.today().isoformat()
p = pathlib.Path("AGENTS.md"); p.write_text(p.read_text(encoding="utf-8").replace("{{所有者名}}", owner), encoding="utf-8")
p = pathlib.Path("current.md"); p.write_text(re.sub(r"最終更新: YYYY-MM-DD", f"最終更新: {today}", p.read_text(encoding="utf-8")), encoding="utf-8")
PY

[ -L CLAUDE.md ] || ln -s AGENTS.md CLAUDE.md
chmod +x scripts/learn-gate.py scripts/session-start.sh
[ -d .git ] || git init -q -b main
git add -A
git commit -qm "ai-memory 初期化(所有者: $owner)" || true

cat <<MSG

初期化しました。次にやること:
  1. profile.md を3〜10行埋める(会社概要・今やっていること)
  2. git remote add origin <URL> && git push -u origin main
  3. Claude Code / Codex / Cursor でこのディレクトリを開く(hooks の有効化を聞かれたら許可)
MSG
