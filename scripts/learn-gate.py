#!/usr/bin/env python3
"""Stop hook: 学習ループを自動で回すためのゲート(Claude Code / Codex / Cursor 共通)。

エージェントがセッションを終えようとしたときに呼ばれ、次のどちらかなら終了を止めて
「学習ループ(AGENTS.md)を実行してからコミット・プッシュせよ」と返す。

  1. この会話のユーザー発言に、差し戻し・書き換え・新しい決定のシグナルがあるのに、
     記憶ファイル(*.md)がこのセッション中に1つも変更されていない
  2. 記憶ファイルに未コミットの変更、または未プッシュのコミットがある

止めるのは1セッションにつき1回だけ。依存は Python 3 標準ライブラリと git のみ。

呼び出し元の判別と入出力:
  - Claude Code / Codex: stdin に stop_hook_active / transcript_path。
      出力 {"decision": "block", "reason": "..."}
  - Cursor: stdin に loop_count / transcript_path(hook_event_name は小文字 "stop")。
      出力 {"followup_message": "..."}
トランスクリプトの形式が読めないエージェントでも、git の未コミット・未プッシュ検知は動く。
"""
import json
import os
import re
import subprocess
import sys

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
    r"\bI (?:already )?told you\b", r"\bas I said\b", r"\bnot that\b", r"\bwrong\b", r"\bremember (?:this|that)\b",
    r"\bdon'?t do that\b", r"\bfrom now on\b", r"\bstop doing\b",
]
SIGNAL_RE = re.compile("|".join(SIGNALS), re.IGNORECASE | re.MULTILINE)
MAX_QUOTES = 5
NOISE = ("<command-name>", "<system-reminder>", "<local-command", "<task-notification", "<ci-monitor-event")


def run(cmd, cwd):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def detect_agent(payload):
    if "loop_count" in payload or str(payload.get("hook_event_name", "")) == "stop":
        return "cursor"
    return "claude"  # Codex は Claude と同じプロトコル


def already_continued(payload, agent):
    if agent == "cursor":
        try:
            return int(payload.get("loop_count") or 0) >= 1
        except (TypeError, ValueError):
            return False
    return bool(payload.get("stop_hook_active"))


def repo_dir(payload):
    for cand in (payload.get("cwd"), *(payload.get("workspace_roots") or [])):
        if cand and os.path.isdir(cand):
            return cand
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _texts_from_content(content):
    if isinstance(content, str):
        return [content]
    out = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                out.append(c.get("text", ""))
            elif isinstance(c, str):
                out.append(c)
    return out


def user_texts(transcript_path):
    """トランスクリプト(JSONL)から、ユーザーが実際に打った本文だけを取り出す。
    Claude Code / Codex 形式({type:user, message:{content}})と、
    {role:user, content} / {role:user, text} の汎用形式を受け付ける。読めなければ空。"""
    texts, first_ts = [], None
    try:
        f = open(transcript_path, encoding="utf-8")
    except (OSError, TypeError):
        return texts, first_ts
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            ts = d.get("timestamp") or d.get("created_at") or d.get("ts")
            if ts and first_ts is None:
                first_ts = ts
            if d.get("isMeta"):
                continue
            msg = d.get("message") if isinstance(d.get("message"), dict) else d
            role = d.get("type") if d.get("type") in ("user", "assistant") else msg.get("role")
            if role != "user":
                continue
            parts = _texts_from_content(msg.get("content"))
            if not parts and isinstance(msg.get("text"), str):
                parts = [msg["text"]]
            for p in parts:
                if any(n in p for n in NOISE):
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
    if since_iso and isinstance(since_iso, str):
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
    if not isinstance(payload, dict):
        payload = {}
    agent = detect_agent(payload)
    if already_continued(payload, agent):
        return 0  # すでに一度止めた。二度は止めない

    repo = repo_dir(payload)
    if not run(["git", "rev-parse", "--is-inside-work-tree"], repo).strip():
        return 0

    texts, first_ts = user_texts(payload.get("transcript_path"))
    hits = [t.replace("\n", " ")[:80] for t in texts if SIGNAL_RE.search(t)]
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
    reason = "\n\n".join(reasons)
    if agent == "cursor":
        print(json.dumps({"followup_message": reason}, ensure_ascii=False))
    else:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
