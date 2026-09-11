# 仕組みの詳細

## なぜ hook が要るのか

`AGENTS.md` に「差し戻されたら学びを書け」と指示するだけでも、AIはある程度従います。ただし会話が長くなると指示は薄れ、本人も「学習して」とは言わないので、実運用では抜けが出ます。作者の運用でも、指示だけの時期は「学習しといて」と毎回言って回収していました。

Claude Code の **Stop hook** はセッション終了の直前に必ず走り、`{"decision":"block","reason":"..."}` を返すと AI は終了できず、`reason` を読んで作業を続けます。ここに学習ループを差し込むと、人が何も言わなくても学習が回ります。

## エージェント別の配線

| | 指示 | スキル | Stop hook の設定 | 続行のさせ方 | 2回目を止めない判定 |
|---|---|---|---|---|---|
| Claude Code | `CLAUDE.md` → `AGENTS.md` | `.claude/skills` → `.agents/skills` | `.claude/settings.json` `hooks.Stop` | `{"decision":"block","reason":…}` | `stop_hook_active` |
| Codex | `AGENTS.md` | `.agents/skills` | `.codex/hooks.json` `hooks.Stop`(Claude と同形式) | 同上 | `stop_hook_active` |
| Cursor | `AGENTS.md` | `.agents/skills` | `.cursor/hooks.json` `hooks.stop` | `{"followup_message":…}`(次のユーザー発言として自動送信) | `loop_count >= 1` |

正本は `AGENTS.md` `.agents/skills/` `scripts/` の3つ。エージェント固有ディレクトリは配線のみ。新しいエージェントを足すときは、そのエージェントの hook 設定から `python3 scripts/learn-gate.py` を呼び、`detect_agent` と出力の分岐を1箇所足せばよい。

## Stop hook(`scripts/learn-gate.py`)の動作

入力(標準入力の JSON): `session_id` `transcript_path` `cwd` `stop_hook_active`(Cursor は `loop_count` `workspace_roots`)

1. 呼び出し元を判別する。`loop_count` があるか `hook_event_name` が小文字の `stop` なら Cursor、それ以外は Claude / Codex
2. `stop_hook_active` が true(Cursor は `loop_count` が1以上)なら何もしない(すでに一度止めた。2回目は止めない。無限ループ防止)
3. `transcript_path` の JSONL を読み、**ユーザーが実際に打った本文だけ**を集める
   - `type == "user"` かつ `isMeta` でないもの(`{role:"user", content}` の汎用形式も受け付ける)
   - `content` の `text` ブロックのみ(`tool_result` は除外)
   - スラッシュコマンド展開(`<command-name>`)・システム注入(`<system-reminder>`)は除外
   - 形式が読めないエージェントでは本文が空になり、次の未コミット・未プッシュ検知だけが効く
4. 本文を `SIGNALS` の正規表現で走査する。差し戻し(「違う」「じゃなくて」「直して」)、「前も言った」系、新しい決定(「今後は」「決めた」)、記憶指示(「覚えて」「学習し」)
5. `git status` と `git log --since=<セッション最初のタイムスタンプ>` で、**このセッション中に変更された .md** を集める
6. 判定
   - シグナルあり **かつ** 記憶の変更なし → block。検知した発言を最大5件引用して「学習ループを実行せよ」
   - 未コミットの変更あり → block。「コミット・プッシュせよ」
   - 未プッシュのコミットあり → block。「push せよ」
   - それ以外 → 何も出さず終了(exit 0)

出力は標準出力に JSON 1行(Claude / Codex は `decision` + `reason`、Cursor は `followup_message`)。exit code は常に 0(exit 2 だと stderr がそのまま AI に渡る別モードになるので使わない)。

## SessionStart hook(`scripts/session-start.sh`)

標準出力がそのままセッションのコンテキストに入ります。出すもの:

- 直近8件の `git log`(何が最近学習されたか)
- `current.md` の最終更新日と経過日数。14日以上なら警告
- 未コミット・未プッシュがあれば警告(前回セッションの学習が途中で切れた可能性)

## テスト

一時リポジトリと偽のトランスクリプトで動作を確認できます。

```bash
tmp=$(mktemp -d) && cd "$tmp" && git init -q -b main && echo "# d" > decisions.md && git add -A && git commit -qm init
now=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
printf '{"type":"user","timestamp":"%s","message":{"role":"user","content":"締めは命令形じゃなくて。前も言ったよね"}}\n' "$now" > t.jsonl
echo "{\"cwd\":\"$tmp\",\"transcript_path\":\"$tmp/t.jsonl\",\"stop_hook_active\":false}" | python3 /path/to/ai-memory/scripts/learn-gate.py
```

`{"decision": "block", ...}` が出れば正常。`stop_hook_active` を `true` にすると何も出ません。

## カスタマイズ

- **検知語**: `SIGNALS` に追加。日本語は部分一致、英語は `\b` で単語境界
- **引用件数**: `MAX_QUOTES`
- **対象ファイル**: `.md` 以外も記憶にするなら `memory_files_changed` と `uncommitted` の拡張子判定を変える
- **止めたくない場面**: 一時的に無効にするなら `.claude/settings.local.json`(gitignore 済み)で `Stop` を空配列に上書きする

## 制約・注意

- hooks はプロジェクト内の設定(`.claude/settings.json` `.codex/hooks.json` `.cursor/hooks.json`)に入っているので、clone した人全員に効きます。初回起動時に各エージェントが有効化の確認を出します。Codex は `~/.codex/config.toml` の `[features] hooks = true` が前提で、2026-09 時点では実験的機能・Windows 非対応
- Cursor の `stop` hook は既定で自動続行5回までの上限があり(`loop_limit`)、このスクリプトは `loop_count` が1以上なら止めないので上限には達しません
- `CLAUDE.md` と `.claude/skills` はシンボリックリンクです。Windows で clone するときは `git config core.symlinks true` を先に設定してください
- トランスクリプトの最初のタイムスタンプを「セッション開始」とみなします。コンパクション後の再開セッションでは古い時刻になることがあり、その場合は学習の催促が出にくくなります(未コミット検知は影響を受けません)
- Windows は `session-start.sh` の `date -j` / `date -d` 判定が効かない場合があります。Git Bash なら `date -d` で動きます
