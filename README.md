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

このサイトには人間の編集部はいません。毎朝6時（日本時間）、GitHub Actions上で **Claude** がWeb検索を使って世界のニュースを収集し、要約・選定・ランキング更新までを自動で行っています。

```
毎朝6:00 JST
   │
   ▼
Claude がWeb検索で世界のニュースを調査（日本・北米・欧州・アジア）
   │
   ▼
要約・ティッカー・ランキングを更新した data.json を生成
   │
   ▼
形式チェックに合格したら自動コミット → サイトに反映 🚲
```

出力は毎回スキーマ検証しており、AIの出力が崩れた日は更新をスキップするだけなので、サイトが壊れることはありません。

## 🛠️ つくり

- **フロントエンド**: 素のHTML/CSS/JS 1ファイル。フレームワークなし、ビルドなし
- **データ**: `data.json` 1ファイル。毎朝これだけが更新されます
- **自動更新**: GitHub Actions + [Claude API](https://docs.claude.com/)（Web検索ツール）
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
scripts/update_news.py             # Claude APIでニュースを収集するスクリプト
.github/workflows/daily-update.yml # 毎朝6時(JST)に実行するGitHub Actions
```

### セットアップ

1. このリポジトリをフォーク
2. **Settings → Pages** で GitHub Pages を有効化（Deploy from a branch / main / root）
3. **Settings → Secrets and variables → Actions** で `ANTHROPIC_API_KEY` を登録（[Anthropic Console](https://console.anthropic.com/) で発行）
4. **Actions** タブ → Daily News Update → **Run workflow** でテスト実行

### コストの目安

1回の実行あたり、Web検索 最大8回（$10/1,000回）＋モデル利用料で数円〜10円程度。毎日実行しても月数百円以内が目安です。

### カスタマイズ

- ニュースの選定方針を変えたい → `scripts/update_news.py` 内のプロンプトを編集
- 手動でデータを直したい → `data.json` を直接編集してコミット（キー構成だけ維持してください）
- 更新時刻を変えたい → `daily-update.yml` の cron を編集（UTC表記なのでJST−9時間）

</details>

---

© 2026 RIDE THE WORLD — さあ、今日もどこかへ走り出そう。🚲
