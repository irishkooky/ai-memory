# 仕組みの詳細

## なぜ hook が要るのか

`AGENTS.md` に「差し戻されたら学びを書け」と指示するだけでも、AIはある程度従います。ただし会話が長くなると指示は薄れ、本人も「学習して」とは言わないので、実運用では抜けが出ます。作者の運用でも、指示だけの時期は「学習しといて」と毎回言って回収していました。

Claude Code の **Stop hook** はセッション終了の直前に必ず走り、`{"decision":"block","reason":"..."}` を返すと AI は終了できず、`reason` を読んで作業を続けます。ここに学習ループを差し込むと、人が何も言わなくても学習が回ります。

## Stop hook(`scripts/learn-gate.py`)の動作

入力(標準入力の JSON): `session_id` `transcript_path` `cwd` `stop_hook_active`

1. `stop_hook_active` が true なら何もしない(すでに一度止めた。2回目は止めない。無限ループ防止)
2. `transcript_path` の JSONL を読み、**ユーザーが実際に打った本文だけ**を集める
   - `type == "user"` かつ `isMeta` でないもの
   - `content` の `text` ブロックのみ(`tool_result` は除外)
   - スラッシュコマンド展開(`<command-name>`)・システム注入(`<system-reminder>`)は除外
3. 本文を `SIGNALS` の正規表現で走査する。差し戻し(「違う」「じゃなくて」「直して」)、「前も言った」系、新しい決定(「今後は」「決めた」)、記憶指示(「覚えて」「学習し」)
4. `git status` と `git log --since=<セッション最初のタイムスタンプ>` で、**このセッション中に変更された .md** を集める
5. 判定
   - シグナルあり **かつ** 記憶の変更なし → block。検知した発言を最大5件引用して「学習ループを実行せよ」
   - 未コミットの変更あり → block。「コミット・プッシュせよ」
   - 未プッシュのコミットあり → block。「push せよ」
   - それ以外 → 何も出さず終了(exit 0)

出力は標準出力に JSON 1行。exit code は常に 0(exit 2 だと stderr がそのまま AI に渡る別モードになるので使わない)。

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

- hooks はプロジェクトの `.claude/settings.json` に入っているので、clone した人全員に効きます。初回起動時に Claude Code が有効化の確認を出します
- トランスクリプトの最初のタイムスタンプを「セッション開始」とみなします。コンパクション後の再開セッションでは古い時刻になることがあり、その場合は学習の催促が出にくくなります(未コミット検知は影響を受けません)
- Windows は `session-start.sh` の `date -j` / `date -d` 判定が効かない場合があります。Git Bash なら `date -d` で動きます
