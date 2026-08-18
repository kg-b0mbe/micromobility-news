# RIDE THE WORLD 🚲

世界のシェアサイクル＆マイクロモビリティニュースを毎朝AIが自動更新するニュースサイト。

## ファイル構成

```
index.html                        # サイト本体（data.jsonを読み込んで描画）
data.json                         # ニュース・ランキングなど全データ（毎朝これだけが更新される）
ogp.png                           # SNSシェア用OGP画像（1200×630）
scripts/update_news.py            # Claude APIでニュースを収集しdata.jsonを更新するスクリプト
.github/workflows/daily-update.yml # 毎朝6時(JST)に上記を実行するGitHub Actions
```

## 自動更新のセットアップ（1回だけ）

1. このリポジトリの **Settings → Secrets and variables → Actions** を開く
2. **New repository secret** で以下を追加
   - Name: `ANTHROPIC_API_KEY`
   - Secret: Anthropic Console（https://console.anthropic.com/）で発行したAPIキー
3. **Actions** タブ → 「Daily News Update」→ **Run workflow** で手動実行してテスト
4. 成功すると `data.json` が更新コミットされ、数分後にサイトに反映される

以後、毎朝6:00（JST）に自動実行されます。

## 仕組み

- `update_news.py` が Claude（claude-sonnet-4-6）にWeb検索ツール付きでニュース収集を依頼
- Claudeが最大8回のWeb検索で日本・北米・欧州・アジアの最新ニュースを調べ、更新後の `data.json` を出力
- スクリプトがJSONの形式（キー構成・件数・URL形式など）を検証してから保存。検証に失敗したら1回だけリトライし、それでもだめなら**何も変更せず終了**（サイトが壊れることはありません）
- GitHub Actionsが差分のある時だけコミット＆プッシュ

## コストの目安

1回の実行あたり: Web検索 最大8回（$10/1,000回 = 最大$0.08）+ モデル利用料（入出力あわせて数円程度）。
毎日実行しても月に数百円以内が目安です。

## 手動でデータを直したいとき

`data.json` を直接編集してコミットすればOKです。スキーマ（キー構成）だけ崩さないように注意してください。
