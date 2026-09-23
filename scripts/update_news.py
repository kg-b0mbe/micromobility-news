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
   - title は60字以内、text は120〜160字程度の日本語要約
   - **url は検索結果に実際に出てきたURLのみ**。決して推測・創作しない。個別記事URLが分からなければそのニュースは採用しない。
2. **ticker**: newsの中から特に目を引く6本を選び、絵文字1つ＋短い一文（35字以内）で書き直す。
3. **featured**: 直近で最もインパクトの大きい1本。既存のものより大きなニュースがなければ据え置き。
4. **pulse**: 各地域の「今いちばんの動き」を stat（20字以内の見出し）と text（120字以内）で表す。**既存の文章に追記しないこと**。更新する場合は、新旧の話題から重要な2〜3点だけを選んで**毎回ゼロから書き直す**。古い話題は削る。
5. **ranking**: 業績発表や大きな動きがあったプレイヤーのdesc・score・tagsを更新。desc は110字以内で**追記せず書き直す**（その事業者の「今」を表す要点だけ）。tags は最大3個・各12字以内の短いキーワード。順位変動は大きなニュースがある場合のみ。各項目の site（公式サイトURL）は変更しない。新しいプレイヤーを追加する場合のみ、検索結果で確認できた公式サイトURLを site に入れる。
6. **stats**: より新しい統計が見つかった場合のみ差し替え。
7. **archive**: newsに含まれる年月（"YYYY.MM"）を新しい順に列挙。
8. **updated**: "{today}" にする。
9. 文体はサイト既存の要約（です・だ調ミックスの簡潔なニュース文体）に合わせる。
10. 事実はすべて検索結果に基づくこと。数字・固有名詞を創作しない。
11. **文字数上限は厳守**。詳しい経緯はニュース欄に任せ、pulse・rankingは「ひと目でわかる要約」に徹する。

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
    for i, r in enumerate(data["ranking"]):
        if "site" in r and not str(r["site"]).startswith("http"):
            raise ValueError(f"ranking[{i}] のsiteが不正: {r['site']}")
    for k in ("date", "title", "text", "url"):
        if k not in data["featured"]:
            raise ValueError(f"featured.{k} がありません")


# ---- 文字数上限（毎日の追記で肥大化しないように） ----
LIMITS = {
    "ticker": 40,
    "pulse.stat": 20,
    "pulse.text": 130,
    "ranking.desc": 120,
    "ranking.meta": 28,
    "ranking.tag": 14,
    "ranking.tags_count": 3,
    "featured.title": 70,
    "featured.text": 220,
    "news.title": 70,
    "news.text": 180,
    "stats.label": 45,
}


def length_issues(data: dict) -> list:
    """上限を超えている項目を人が読める形で列挙する。"""
    L, out = LIMITS, []
    def chk(label, text, lim):
        if len(str(text)) > lim:
            out.append(f"{label}: {len(str(text))}字（上限{lim}字）")
    for i, t in enumerate(data["ticker"]):
        chk(f"ticker[{i}]", t, L["ticker"])
    for p in data["pulse"]:
        chk(f"pulse[{p.get('cls')}].stat", p["stat"], L["pulse.stat"])
        chk(f"pulse[{p.get('cls')}].text", p["text"], L["pulse.text"])
    for r in data["ranking"]:
        n = r.get("name")
        chk(f"ranking[{n}].desc", r["desc"], L["ranking.desc"])
        chk(f"ranking[{n}].meta", r["meta"], L["ranking.meta"])
        if len(r["tags"]) > L["ranking.tags_count"]:
            out.append(f"ranking[{n}].tags: {len(r['tags'])}個（上限{L['ranking.tags_count']}個）")
        for t in r["tags"]:
            chk(f"ranking[{n}].tag「{t[:10]}…」", t, L["ranking.tag"])
    chk("featured.title", data["featured"]["title"], L["featured.title"])
    chk("featured.text", data["featured"]["text"], L["featured.text"])
    for i, n in enumerate(data["news"]):
        chk(f"news[{i}].title", n["title"], L["news.title"])
        chk(f"news[{i}].text", n["text"], L["news.text"])
    for i, st in enumerate(data["stats"]):
        chk(f"stats[{i}].label", st["label"], L["stats.label"])
    return out


def clip(text: str, lim: int) -> str:
    """上限内に収める。できるだけ文の区切り（。）で切る。"""
    text = str(text)
    if len(text) <= lim:
        return text
    cut = text[:lim]
    pos = cut.rfind("。")
    if pos >= lim * 0.5:
        return cut[: pos + 1]
    return cut[: lim - 1] + "…"


def enforce_limits(data: dict) -> None:
    """AIが上限を守れなかった場合の最終手段。"""
    L = LIMITS
    data["ticker"] = [clip(t, L["ticker"]) for t in data["ticker"]]
    for p in data["pulse"]:
        p["stat"] = clip(p["stat"], L["pulse.stat"])
        p["text"] = clip(p["text"], L["pulse.text"])
    for r in data["ranking"]:
        r["desc"] = clip(r["desc"], L["ranking.desc"])
        r["meta"] = clip(r["meta"], L["ranking.meta"])
        r["tags"] = [clip(t, L["ranking.tag"]) for t in r["tags"][: L["ranking.tags_count"]]]
    data["featured"]["title"] = clip(data["featured"]["title"], L["featured.title"])
    data["featured"]["text"] = clip(data["featured"]["text"], L["featured.text"])
    for n in data["news"]:
        n["title"] = clip(n["title"], L["news.title"])
        n["text"] = clip(n["text"], L["news.text"])
    for st in data["stats"]:
        st["label"] = clip(st["label"], L["stats.label"])


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
            issues = length_issues(data)
            if issues and attempt == 0:
                print(f"  文字数オーバー {len(issues)}件 → 書き直しを依頼", file=sys.stderr)
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({
                    "role": "user",
                    "content": (
                        "以下の項目が文字数上限を超えています。追記ではなく要点だけに絞って書き直し、"
                        "data.json全体をJSONのみで出力し直してください。\n- " + "\n- ".join(issues[:40])
                    ),
                })
                continue
            if issues:
                print(f"  文字数オーバー {len(issues)}件を自動で切り詰めました", file=sys.stderr)
                enforce_limits(data)
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
