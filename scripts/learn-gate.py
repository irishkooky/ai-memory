#!/usr/bin/env python3
"""Stop hook: 学習ループを自動で回すためのゲート。

Claude Code がセッションを終えようとしたときに呼ばれ、次のどちらかなら終了を止めて
「学習ループ(AGENTS.md)を実行してからコミット・プッシュせよ」と Claude に返す。

  1. この会話のユーザー発言に、差し戻し・書き換え・新しい決定のシグナルがあるのに、
     記憶ファイル(*.md)がこのセッション中に1つも変更されていない
  2. 記憶ファイルに未コミットの変更、または未プッシュのコミットがある

止めるのは1セッションにつき1回だけ(stop_hook_active=True のときは何もしない)。
依存は Python 3 標準ライブラリと git のみ。
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

# 差し戻し・書き換え・「前も言った」・新しい決定のシグナル。必要に応じて追加する。
SIGNALS = [
    # 差し戻し・否定
    r"違う", r"そうじゃな", r"じゃなくて", r"ではなく", r"やめて", r"戻して", r"ダメ", r"だめ",
    r"直して", r"直した", r"書き換え", r"修正した", r"変えた", r"却下", r"ボツ", r"長い", r"短くして",
    # 「前も言った」
    r"前も言", r"前に言", r"言ったよね", r"言ったはず", r"言ってる", r"何回も", r"何度も", r"また同じ", r"散々",
    r"前回も", r"いつも言", r"忘れ",
    # 新しい決定・記憶指示
    r"今後は", r"これからは", r"決めた", r"決定", r"にする$", r"にします", r"覚えて", r"覚えといて",
    r"学習し", r"記録し", r"メモし", r"ルールに",
    # 英語
    r"\bI (already )?told you\b", r"\bas I said\b", r"\bnot that\b", r"\bwrong\b", r"\bremember (this|that)\b",
    r"\bdon'?t do that\b", r"\bfrom now on\b", r"\bstop doing\b",
]
SIGNAL_RE = re.compile("|".join(SIGNALS), re.IGNORECASE | re.MULTILINE)
MAX_QUOTES = 5


def run(cmd, cwd):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def user_texts(transcript_path):
    """トランスクリプト(JSONL)から、ユーザーが実際に打った本文だけを取り出す。"""
    texts, first_ts = [], None
    try:
        f = open(transcript_path, encoding="utf-8")
    except OSError:
        return texts, first_ts
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("timestamp")
            if ts and first_ts is None:
                first_ts = ts
            if d.get("type") != "user" or d.get("isMeta"):
                continue
            content = d.get("message", {}).get("content")
            parts = []
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
            for p in parts:
                # スラッシュコマンド展開・システム注入・ツール結果は対象外
                if "<command-name>" in p or "<system-reminder>" in p or "<local-command" in p:
                    continue
                p = p.strip()
                if p:
                    texts.append(p)
    return texts, first_ts


def memory_files_changed(repo, since_iso):
    """このセッション中に変更(コミット済み or 未コミット)された .md ファイル一覧。"""
    changed = set()
    for line in run(["git", "status", "--porcelain", "--untracked-files=all"], repo).splitlines():
        path = line[3:].strip()
        if path.endswith(".md"):
            changed.add(path)
    if since_iso:
        out = run(["git", "log", f"--since={since_iso}", "--name-only", "--pretty=format:"], repo)
        for path in out.splitlines():
            if path.strip().endswith(".md"):
                changed.add(path.strip())
    return sorted(changed)


def uncommitted(repo):
    return [l for l in run(["git", "status", "--porcelain", "--untracked-files=all"], repo).splitlines() if l.strip()]


def unpushed(repo):
    out = run(["git", "rev-list", "--count", "@{upstream}..HEAD"], repo).strip()
    return int(out) if out.isdigit() else 0


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if payload.get("stop_hook_active"):
        return 0  # すでに一度止めた。二度は止めない

    repo = payload.get("cwd") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not run(["git", "rev-parse", "--is-inside-work-tree"], repo).strip():
        return 0

    texts, first_ts = user_texts(payload.get("transcript_path", ""))
    hits = []
    for t in texts:
        if SIGNAL_RE.search(t):
            hits.append(t.replace("\n", " ")[:80])
    changed = memory_files_changed(repo, first_ts)
    dirty = uncommitted(repo)
    ahead = unpushed(repo)

    reasons = []
    if hits and not changed:
        quoted = "\n".join(f"  - 「{h}」" for h in hits[-MAX_QUOTES:])
        reasons.append(
            "この会話に差し戻し・書き換え・新しい決定と思われるユーザー発言があるのに、記憶ファイルが1つも更新されていません。\n"
            f"検知した発言(直近{min(len(hits), MAX_QUOTES)}件):\n{quoted}\n"
            "AGENTS.md の「学習ループ」に従い、次回に効く一般ルール/事実として該当ファイル"
            "(feedback.md / decisions.md / profile.md / people/ / projects/)に日付付きで追記し、コミット・プッシュしてから終了してください。"
            "読み返して追記が不要なら、その理由を一言述べて終了して構いません。"
        )
    if dirty:
        reasons.append("記憶ファイルに未コミットの変更があります:\n" + "\n".join(f"  {l}" for l in dirty[:15]) +
                       "\n内容を確認してコミット・プッシュしてください(作業ブランチ運用の環境では main へのマージまで)。")
    elif ahead:
        reasons.append(f"未プッシュのコミットが {ahead} 件あります。git push してください。")

    if not reasons:
        return 0
    print(json.dumps({"decision": "block", "reason": "\n\n".join(reasons)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
