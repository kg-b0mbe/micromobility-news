#!/usr/bin/env python3
"""
RIDE THE WORLD — 毎朝のニュース自動更新スクリプト

Claude API（Web検索ツール付き）で世界のマイクロモビリティニュースを収集し、
data.json を更新します。GitHub Actions から毎朝実行される想定です。

必要な環境変数:
  ANTHROPIC_API_KEY : Anthropic APIキー
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data.json"
MODEL = "claude-sonnet-4-6"
MAX_SEARCHES = 8

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).strftime("%Y.%m.%d")

REQUIRED_KEYS = {"updated", "ticker", "pulse", "ranking", "featured", "news", "stats", "archive"}
NEWS_KEYS = {"region", "date", "title", "text", "url"}
VALID_REGIONS = {"jp", "na", "eu", "as", "gl"}

PROMPT = """あなたは「RIDE THE WORLD」— 世界のシェアサイクル＆マイクロモビリティニュースサイトの編集者AIです。

今日の日付（日本時間）: {today}

以下が現在のサイトデータ（data.json）です:

```json
{current}
```

# タスク
Web検索を使って、世界のシェアサイクル・電動キックボード・eバイクシェアなどマイクロモビリティに関する最新ニュース（直近1週間を中心に）を調べ、data.jsonを更新してください。

検索の観点（すべて検索する必要はなく、効率よく最大{max_searches}回まで）:
- 日本: シェアサイクル ニュース / Luup / HELLO CYCLING / NOLL など
- 海外: bike share news / e-scooter news / Lime / Voi / Dott / Bird / Citi Bike / Vélib' など
- 業界: micromobility news

# 更新ルール
1. **news**: 新しいニュースがあれば先頭に追加し、古いものを削除して常に14〜16件・日付降順に保つ。既存項目の書き換えは不要。
   - region は jp / na / eu / as / gl のいずれか
   - date は "YYYY.MM.DD" 形式
   - text は120〜160字程度の日本語要約
   - **url は検索結果に実際に出てきたURLのみ**。決して推測・創作しない。個別記事URLが分からなければそのニュースは採用しない。
2. **ticker**: newsの中から特に目を引く6本を選び、絵文字1つ＋短い一文（30字以内）で書き直す。
3. **featured**: 直近で最もインパクトの大きい1本。既存のものより大きなニュースがなければ据え置き。
4. **pulse**: 各地域のtext/statを最新の状況に合わせて微修正（大きな変化がなければ据え置き）。
5. **ranking**: 業績発表や大きな動きがあったプレイヤーのdesc・score・tagsを更新。順位変動は大きなニュースがある場合のみ。
6. **stats**: より新しい統計が見つかった場合のみ差し替え。
7. **archive**: newsに含まれる年月（"YYYY.MM"）を新しい順に列挙。
8. **updated**: "{today}" にする。
9. 文体はサイト既存の要約（です・だ調ミックスの簡潔なニュース文体）に合わせる。
10. 事実はすべて検索結果に基づくこと。数字・固有名詞を創作しない。

# 出力形式
更新後のdata.json全体を、**JSONのみ**で出力してください。コードフェンスや説明文は不要です。
必ず既存と同じキー構成（updated, ticker, pulse, ranking, featured, news, stats, archive）を保ってください。
"""


def extract_json(text: str) -> dict:
    """レスポンステキストからJSONオブジェクトを取り出す。"""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("JSONオブジェクトが見つかりません")
    return json.loads(text[start : end + 1])


def validate(data: dict) -> None:
    missing = REQUIRED_KEYS - set(data)
    if missing:
        raise ValueError(f"キーが不足: {missing}")
    if not (10 <= len(data["news"]) <= 20):
        raise ValueError(f"newsの件数が不正: {len(data['news'])}")
    for i, n in enumerate(data["news"]):
        if NEWS_KEYS - set(n):
            raise ValueError(f"news[{i}] のキーが不足: {NEWS_KEYS - set(n)}")
        if n["region"] not in VALID_REGIONS:
            raise ValueError(f"news[{i}] のregionが不正: {n['region']}")
        if not str(n["url"]).startswith("http"):
            raise ValueError(f"news[{i}] のurlが不正: {n['url']}")
    if not (3 <= len(data["ticker"]) <= 8):
        raise ValueError("tickerは3〜8件にしてください")
    if len(data["pulse"]) != 4:
        raise ValueError("pulseは4地域必要です")
    if not (6 <= len(data["ranking"]) <= 10):
        raise ValueError("rankingは6〜10件にしてください")
    for k in ("date", "title", "text", "url"):
        if k not in data["featured"]:
            raise ValueError(f"featured.{k} がありません")


def response_text(resp) -> str:
    return "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY が設定されていません", file=sys.stderr)
        return 1

    current = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    client = anthropic.Anthropic()

    prompt = PROMPT.format(
        today=TODAY,
        current=json.dumps(current, ensure_ascii=False, indent=2),
        max_searches=MAX_SEARCHES,
    )

    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}]

    last_error = None
    for attempt in range(2):
        print(f"[{attempt + 1}回目] Claudeにニュース収集を依頼中...")
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            messages=messages,
            tools=tools,
        )
        text = response_text(resp)
        try:
            data = extract_json(text)
            validate(data)
            break
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            print(f"  出力の検証に失敗: {e}", file=sys.stderr)
            messages.append({"role": "assistant", "content": resp.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"出力の検証に失敗しました: {e}\n"
                        "説明文やコードフェンスを付けず、指定スキーマの有効なJSONのみを出力し直してください。"
                    ),
                }
            )
    else:
        print(f"ERROR: 2回試行しましたが有効なデータを得られませんでした: {last_error}", file=sys.stderr)
        return 1

    data["updated"] = TODAY

    if data == current:
        print("変更なし。data.jsonは更新しませんでした。")
        return 0

    DATA_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"data.json を更新しました（ニュース {len(data['news'])} 件 / {TODAY}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
