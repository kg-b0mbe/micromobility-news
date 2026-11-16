# 🚲 RIDE THE WORLD

**世界のまちを走るニュースを、毎日ここから。**

[![Daily News Update](https://github.com/kg-b0mbe/micromobility-news/actions/workflows/daily-update.yml/badge.svg)](https://github.com/kg-b0mbe/micromobility-news/actions/workflows/daily-update.yml)

シェアサイクル・電動キックボード・eバイク——世界中で急成長する「マイクロモビリティ」のニュースを、**毎朝AIが自動収集・自動更新**する日本語ニュースサイトです。

### 👉 [サイトを見る → RIDE THE WORLD](https://kg-b0mbe.github.io/micromobility-news/)

---

## 🌍 このサイトでわかること

| セクション | 内容 |
|---|---|
| 🕗 **ニュースティッカー** | いま話題のトピックが、自転車と一緒に道路を流れていきます |
| 🗺️ **WORLD PULSE** | 日本・北米・欧州・アジア、4地域の「今」をひと目で |
| 🏆 **GLOBAL PLAYERS** | Lime、Vélib'、Citi Bike、Luup…世界の注目プレイヤーを独自スコアでランキング。公式サイトへのリンク付き |
| 📰 **LATEST NEWS** | 世界の最新ニュース。地域ボタンでサクッと絞り込み |
| 📊 **BY THE NUMBERS** | 市場規模や利用回数など、業界を数字で俯瞰 |

たとえば——LimeのNASDAQ上場、パリVélib'が中国圏外で世界一の利用数、日本のドコモ・バイクシェアの「NOLL」への刷新、Luupの夜割やみまもり機能。日本のニュースと世界のニュースを、ひとつの画面で追いかけられます。

## 🤖 毎朝、AIが編集しています

このサイトには人間の編集部はいません。毎朝6時（日本時間）、自宅の **DGX Spark で動くローカルLLM** が世界のニュースを選定・要約し、ティッカーやランキングまで自動で更新しています。

```
毎朝6:00 JST
   │
   ▼
スクリプトがニュース検索・RSSで候補を集める（日本・北米・欧州・アジア）
   │
   ▼
ローカルLLMが候補を選び、記事本文を読んで1本ずつ要約
   │
   ▼
ティッカー・地域パルス・ランキングを更新した data.json を生成
   │
   ▼
形式チェックに合格したら自動コミット → サイトに反映 🚲
```

記事URLは検索結果からプログラムが埋めるので、AIがURLを創作することはありません。出力は毎回スキーマ検証しており、AIの出力が崩れた日は更新をスキップするだけなので、サイトが壊れることはありません。

## 🛠️ つくり

- **フロントエンド**: 素のHTML/CSS/JS 1ファイル。フレームワークなし、ビルドなし
- **データ**: `data.json` 1ファイル。毎朝これだけが更新されます
- **自動更新**: 自宅マシンの cron + ローカルLLM（OpenAI互換API）＋ DuckDuckGo News / RSS
- **ホスティング**: GitHub Pages

デザインテーマは「晴れた日の自転車レーン」。ページ上部の道路では、車輪を回しながら自転車が走り続けています。

## 💬 ニュースの間違いを見つけたら

AIによる自動収集のため、要約に不正確な点が含まれる可能性があります。おかしな点を見つけたら [Issues](https://github.com/kg-b0mbe/micromobility-news/issues) で教えてください。各ニュースの「READ MORE →」から必ず一次ソースを確認できます。

---

<details>
<summary>🔧 開発者向け: 自分でも動かしたい人へ</summary>

### ファイル構成

```
index.html                         # サイト本体（data.jsonを読み込んで描画）
data.json                          # ニュース・ランキングなど全データ
ogp.png                            # SNSシェア用OGP画像（1200×630）
scripts/update_news.py             # ニュースを集めてローカルLLMで更新するスクリプト
scripts/run_local.sh               # 毎朝cronで実行（pull → 更新 → push）
scripts/.env.example               # LLMサーバーなどの設定例
.github/workflows/daily-update.yml # 手動実行用のGitHub Actions
```

### セットアップ（ローカルLLM）

OpenAI互換API（vLLM / SGLang / llama.cpp / Ollama など）で動くLLMサーバーがあれば動きます。
実行するマシン（例: 常時起動のMac miniやDGX Spark）で:

```sh
git clone git@github.com:<you>/micromobility-news.git && cd micromobility-news
python3 -m venv .venv && .venv/bin/pip install ddgs
cp scripts/.env.example scripts/.env   # LLM_BASE_URL / LLM_MODEL / LLM_API_KEY を書く
.venv/bin/python scripts/update_news.py --dry-run   # 書き込まずに試す
scripts/run_local.sh                                # 本番（更新してpush）
```

毎朝6時に回すには cron に登録します（マシンのタイムゾーンがJSTの場合）:

```
0 6 * * * /path/to/micromobility-news/scripts/run_local.sh >> /tmp/ride-the-world.log 2>&1
```

push には、このリポジトリだけに書き込める **Deploy key**（Settings → Deploy keys、Allow write access）を使うのがおすすめです。

### 処理の流れ

1. `ddgs`（DuckDuckGo News）と RSS で直近1週間の候補記事を集め、掲載済みURLと重複を除く
2. LLMが候補一覧から載せる記事を最大6本選ぶ（候補IDで返すのでURLは創作できない）
3. 選んだ記事の本文を取得し、LLMが1本ずつ要約（本文に無い数字は使わない指示）
4. LLMがニュース一覧からティッカー・地域パルス・注目記事・ランキング説明を更新
5. 文字数・スキーマを検証して `data.json` を保存（`stats` は手動更新）

検索を自前の SearXNG にしたい場合は `.env` で `SEARCH_BACKEND=searxng` と `SEARXNG_URL` を設定します。

### コストの目安

ローカルLLMなのでAPI料金はかかりません（電気代のみ）。

### 表示・フィルタのテスト

Node.js 18以降で、依存パッケージを追加せずに実行できます。

```sh
node --test tests/news-filters.test.cjs
```

インラインの表示スクリプトを軽量DOMハーネスで検証します（ブラウザのレイアウト検証は別途必要です）。
年月は現在の `news` にある日付から生成し、地域と組み合わせて絞り込みます。
地域ボタンの件数は選択した年月内の件数です。地域の `ALL` は年月を維持し、
「全件表示に戻る」は地域・年月の両方を解除します。記事の履歴保存は行いません。

### カスタマイズ

- ニュースの選定方針を変えたい → `scripts/update_news.py` 内のプロンプトを編集
- 手動でデータを直したい → `data.json` を直接編集してコミット（キー構成だけ維持してください）
- 更新時刻を変えたい → `daily-update.yml` の cron を編集（UTC表記なのでJST−9時間）

</details>

---

© 2026 RIDE THE WORLD — さあ、今日もどこかへ走り出そう。🚲
