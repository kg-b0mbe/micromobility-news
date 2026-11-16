#!/usr/bin/env python3
"""
RIDE THE WORLD — 毎朝のニュース自動更新スクリプト（ローカルLLM版）

ニュース検索は Python 側で行い（DuckDuckGo News / SearXNG / RSS）、
LLM には「選定・要約・見出しづくり」だけを任せます。
LLM は OpenAI 互換 API（vLLM / SGLang / llama.cpp / Ollama など）で呼び出すので、
DGX Spark などのローカルLLMでそのまま動きます。

URL は検索結果から Python が埋めるため、LLM が URL を創作する余地はありません。

必要な環境変数:
  LLM_BASE_URL   : OpenAI互換APIのベースURL（例: http://spark:8000/v1）
  LLM_MODEL      : モデル名（サーバー側の served model name）
任意:
  LLM_API_KEY    : APIキー（不要なサーバーなら空でOK）
  LLM_JSON_MODE  : 1 なら response_format=json_object を送る（既定: 1）
  LLM_EXTRA_BODY : リクエストに混ぜるJSON（例: {"chat_template_kwargs":{"enable_thinking":false}}）
  LLM_TIMEOUT    : 1リクエストのタイムアウト秒（既定: 900）
  SEARCH_BACKEND : ddgs（既定） / searxng / none（RSSのみ）
  SEARXNG_URL    : SEARCH_BACKEND=searxng のときの SearXNG のURL

使い方:
  python3 scripts/update_news.py              # 通常実行（data.json を更新）
  python3 scripts/update_news.py --dry-run    # 書き込まずに結果を表示
  python3 scripts/update_news.py --candidates-file c.json  # 検索の代わりに候補JSONを使う（テスト用）
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import email.utils
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data.json"

JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
TODAY = NOW.strftime("%Y.%m.%d")

LOOKBACK_DAYS = 8          # これより古い候補は捨てる
MAX_CANDIDATES = 45        # LLMに見せる候補の上限
MAX_NEW_ITEMS = 6          # 1日に追加するニュースの上限
NEWS_KEEP = 15             # news に残す件数
EXCERPT_CHARS = 2500       # 要約時にLLMへ渡す本文の長さ
UA = "Mozilla/5.0 (compatible; RideTheWorldBot/2.0; +https://kg-b0mbe.github.io/micromobility-news/)"

REQUIRED_KEYS = {"updated", "ticker", "pulse", "ranking", "featured", "news", "stats", "archive"}
NEWS_KEYS = {"region", "date", "title", "text", "url"}
VALID_REGIONS = {"jp", "na", "eu", "as", "gl"}
PULSE_REGIONS = ["jp", "na", "eu", "as"]

# ---------------------------------------------------------------------------
# 検索クエリとRSS
# ---------------------------------------------------------------------------
SEARCH_QUERIES = [
    # (クエリ, ddgsのregion)
    ("シェアサイクル", "jp-jp"),
    ("LUUP 電動キックボード", "jp-jp"),
    ("HELLO CYCLING", "jp-jp"),
    ("ドコモ・バイクシェア NOLL", "jp-jp"),
    ("bike share", "us-en"),
    ("e-scooter sharing", "uk-en"),
    ("Lime scooter", "us-en"),
    ("Voi OR Dott scooters", "uk-en"),
    ("Citi Bike", "us-en"),
    ("Vélib", "fr-fr"),
    ("micromobility", "us-en"),
    ("bike sharing Asia YouBike OR Hellobike OR Anywheel", "us-en"),
]

RSS_FEEDS = [
    "https://zagdaily.com/feed/",
    "https://micromobility.substack.com/feed",
]


# ---------------------------------------------------------------------------
# 小物
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def http_get(url: str, timeout: int = 15, max_bytes: int = 2_000_000) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes), r.headers.get("Content-Type", "")


def decode_html(raw: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    enc = m.group(1) if m else None
    if not enc:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4000], re.I)
        enc = m.group(1).decode() if m else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def parse_date(value) -> str | None:
    """いろいろな日付表記を 'YYYY.MM.DD'（JST）にする。"""
    if not value:
        return None
    s = str(value).strip()
    dt = None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            m = re.search(r"(20\d\d)[./年-](\d{1,2})[./月-](\d{1,2})", s)
            if m:
                dt = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=JST)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime("%Y.%m.%d")


def is_recent(date_str: str | None) -> bool:
    if not date_str:
        return True  # 日付不明は一旦残し、本文から判断させる
    try:
        d = datetime.strptime(date_str, "%Y.%m.%d").replace(tzinfo=JST)
    except ValueError:
        return True
    return NOW - timedelta(days=LOOKBACK_DAYS) <= d <= NOW + timedelta(days=1)


def norm_url(url: str) -> str:
    """重複判定用にURLを正規化（クエリの追跡パラメータ・末尾スラッシュ・www を落とす）。"""
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
    host = re.sub(r"^www\.", "", p.netloc.lower())
    return urllib.parse.urlunsplit(("https", host, p.path.rstrip("/"), urllib.parse.urlencode(q), ""))


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ---------------------------------------------------------------------------
# 候補収集
# ---------------------------------------------------------------------------
def search_ddgs() -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        log("  ddgs が未インストールです（pip install ddgs）。検索をスキップします。")
        return []
    out = []
    with DDGS() as ddg:
        for q, region in SEARCH_QUERIES:
            try:
                rows = ddg.news(q, region=region, timelimit="w", max_results=10) or []
            except Exception as e:  # レート制限などは他のクエリで補う
                log(f"  検索失敗「{q}」: {e}")
                continue
            for r in rows:
                out.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "source": r.get("source", ""),
                    "date": parse_date(r.get("date")),
                    "snippet": r.get("body", ""),
                    "query": q,
                })
    return out


def search_searxng() -> list[dict]:
    base = os.environ.get("SEARXNG_URL", "").rstrip("/")
    if not base:
        log("  SEARXNG_URL が未設定です。検索をスキップします。")
        return []
    out = []
    for q, region in SEARCH_QUERIES:
        lang = "ja" if region.startswith("jp") else ("fr" if region.startswith("fr") else "en")
        url = f"{base}/search?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "categories": "news", "time_range": "week", "language": lang}
        )
        try:
            raw, _ = http_get(url, timeout=30)
            rows = json.loads(raw).get("results", [])[:10]
        except Exception as e:
            log(f"  検索失敗「{q}」: {e}")
            continue
        for r in rows:
            out.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "source": urllib.parse.urlsplit(r.get("url", "")).netloc,
                "date": parse_date(r.get("publishedDate")),
                "snippet": r.get("content", ""),
                "query": q,
            })
    return out


def fetch_rss() -> list[dict]:
    out = []
    for feed in RSS_FEEDS:
        try:
            raw, _ = http_get(feed, timeout=20)
            root = ET.fromstring(raw)
        except Exception as e:
            log(f"  RSS取得失敗 {feed}: {e}")
            continue
        for item in root.iter("item"):
            out.append({
                "title": strip_tags(item.findtext("title", "")),
                "url": (item.findtext("link") or "").strip(),
                "source": urllib.parse.urlsplit(feed).netloc,
                "date": parse_date(item.findtext("pubDate")),
                "snippet": strip_tags(item.findtext("description", ""))[:400],
                "query": "rss",
            })
    return out


def collect_candidates(current: dict) -> list[dict]:
    backend = os.environ.get("SEARCH_BACKEND", "ddgs").lower()
    rows = []
    if backend == "ddgs":
        rows += search_ddgs()
    elif backend == "searxng":
        rows += search_searxng()
    rows += fetch_rss()
    return dedupe_candidates(rows, current)


def dedupe_candidates(rows: list[dict], current: dict) -> list[dict]:
    seen = {norm_url(n["url"]) for n in current["news"]}
    seen.add(norm_url(current["featured"]["url"]))
    seen_titles = {re.sub(r"\W", "", n["title"])[:30] for n in current["news"]}
    out = []
    for r in rows:
        url = r.get("url") or ""
        if not url.startswith("http"):
            continue
        key = norm_url(url)
        tkey = re.sub(r"\W", "", r.get("title", ""))[:30]
        if key in seen or (tkey and tkey in seen_titles):
            continue
        r["date"] = parse_date(r.get("date"))
        if not is_recent(r.get("date")):
            continue
        seen.add(key)
        seen_titles.add(tkey)
        out.append(r)
    out.sort(key=lambda r: r.get("date") or "", reverse=True)
    out = out[:MAX_CANDIDATES]
    for i, r in enumerate(out):
        r["id"] = f"c{i}"
    return out


# ---------------------------------------------------------------------------
# 記事本文の取得（LLMが要約の根拠にする）
# ---------------------------------------------------------------------------
class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.paras = []
        self._in_p = 0
        self._skip = 0
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k and a.get("content"):
                self.meta.setdefault(k, a["content"])
        elif tag in ("script", "style", "nav", "footer", "header", "aside"):
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._in_p += 1
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header", "aside") and self._skip:
            self._skip -= 1
        elif tag == "p" and self._in_p:
            self._in_p -= 1
            t = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if len(t) >= 25:
                self.paras.append(t)

    def handle_data(self, data):
        if self._in_p and not self._skip:
            self._buf.append(data)


def fetch_article(c: dict) -> dict:
    """本文抜粋と公開日を取得して候補に足す。失敗したらスニペットだけで続行。"""
    try:
        raw, ctype = http_get(c["url"], timeout=15)
        p = _ArticleParser()
        p.feed(decode_html(raw, ctype))
        desc = p.meta.get("og:description") or p.meta.get("description") or ""
        body = " ".join(p.paras)
        c["excerpt"] = (desc + "\n" + body).strip()[:EXCERPT_CHARS]
        c["date"] = c.get("date") or parse_date(
            p.meta.get("article:published_time") or p.meta.get("og:published_time") or p.meta.get("date")
        )
    except Exception as e:
        log(f"  本文取得失敗 {c['url'][:80]}: {e}")
        c["excerpt"] = ""
    return c


# ---------------------------------------------------------------------------
# LLM（OpenAI互換API）
# ---------------------------------------------------------------------------
class LLM:
    def __init__(self):
        self.base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "")
        self.key = os.environ.get("LLM_API_KEY", "")
        self.json_mode = os.environ.get("LLM_JSON_MODE", "1") == "1"
        self.timeout = int(os.environ.get("LLM_TIMEOUT", "900"))
        self.extra = json.loads(os.environ.get("LLM_EXTRA_BODY") or "{}")
        if not self.base or not self.model:
            raise SystemExit("ERROR: LLM_BASE_URL と LLM_MODEL を設定してください")

    def chat(self, messages: list[dict], max_tokens: int = 4000) -> str:
        body = {"model": self.model, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(self.extra)
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(
            self.base + "/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            resp = json.loads(r.read())
        text = resp["choices"][0]["message"].get("content") or ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

    def json(self, system: str, user: str, check=None, max_tokens: int = 4000) -> dict:
        """JSONを返させる。壊れていたら、または check() が問題を返したら1回だけ直させる。"""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last = None
        for attempt in range(2):
            text = self.chat(messages, max_tokens=max_tokens)
            try:
                data = extract_json(text)
                problems = check(data) if check else []
            except (ValueError, json.JSONDecodeError) as e:
                data, problems = None, [f"JSONとして読めません: {e}"]
            if not problems:
                return data
            last = (data, problems)
            log(f"    出力に問題 {len(problems)}件 → 書き直しを依頼" if attempt == 0 else f"    問題が残りました: {problems[:3]}")
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "次の問題を直して、JSONのみで出力し直してください。\n- " + "\n- ".join(problems[:30])},
            ]
        if last and last[0] is not None:
            return last[0]  # 文字数超過などは後段で切り詰める
        raise ValueError(f"LLMから有効なJSONを得られませんでした: {last[1] if last else ''}")


def extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("JSONオブジェクトが見つかりません")
    return json.loads(text[start : end + 1])


SYSTEM = (
    "あなたは「RIDE THE WORLD」— 世界のシェアサイクル＆マイクロモビリティニュースサイトの編集者です。"
    "与えられた資料だけを根拠に書き、数字・固有名詞・日付を創作しません。"
    "出力は必ず指定されたJSONのみ。説明文やコードフェンスは付けません。"
)


# ---- ステップ1: 候補から採用ニュースを選ぶ ----------------------------------
def step_select(llm: LLM, current: dict, cands: list[dict]) -> list[dict]:
    existing = "\n".join(f"- {n['date']} {n['title']}" for n in current["news"])
    lines = "\n".join(
        f"[{c['id']}] {c.get('date') or '日付不明'} | {c.get('source','')} | {c['title']}\n    {strip_tags(c.get('snippet',''))[:220]}"
        for c in cands
    )
    ids = {c["id"] for c in cands}

    def check(d):
        sel = d.get("selected")
        if not isinstance(sel, list):
            return ["selected 配列がありません"]
        probs = []
        for s in sel:
            if s.get("id") not in ids:
                probs.append(f"存在しないid: {s.get('id')}")
            if s.get("region") not in VALID_REGIONS:
                probs.append(f"regionが不正: {s.get('region')}")
        return probs

    user = f"""今日の日付（日本時間）: {TODAY}

# サイトに掲載済みのニュース（これと同じ話題は選ばない）
{existing}

# 今日の候補記事
{lines}

# タスク
候補から、サイトに新しく載せる価値のあるニュースを最大{MAX_NEW_ITEMS}本選んでください。
- シェアサイクル・電動キックボード・eバイクシェアなどマイクロモビリティ業界のニュースに限る（個人向け自転車の新製品レビュー、事故の個別報道、広告記事は除く）
- 掲載済みと同じ話題、候補同士で重複する話題は1本にまとめる（より詳しそうな方を選ぶ）
- 直近1週間のもの。日本・北米・欧州・アジアが偏りすぎないように
- 該当がなければ空配列でよい
- region: jp=日本 / na=北米 / eu=欧州 / as=アジア太平洋 / gl=世界・複数地域

# 出力形式
{{"selected": [{{"id": "c0", "region": "jp"}}]}}"""
    data = llm.json(SYSTEM, user, check=check, max_tokens=1500)
    by_id = {c["id"]: c for c in cands}
    out, used = [], set()
    for s in data.get("selected", [])[:MAX_NEW_ITEMS]:
        c = by_id.get(s.get("id"))
        if c and c["id"] not in used and s.get("region") in VALID_REGIONS:
            used.add(c["id"])
            out.append(dict(c, region=s["region"]))
    return out


# ---- ステップ2: 1本ずつ要約 ------------------------------------------------
def step_summarize(llm: LLM, c: dict) -> dict | None:
    source_text = (c.get("excerpt") or "").strip() or strip_tags(c.get("snippet", ""))
    if len(source_text) < 60:
        log(f"  本文が短すぎるため見送り: {c['title'][:50]}")
        return None

    def check(d):
        if d.get("skip"):
            return []
        probs = []
        for k in ("title", "text"):
            if not d.get(k):
                probs.append(f"{k} がありません")
        if len(d.get("title", "")) > LIMITS["news.title"]:
            probs.append(f"title が{len(d['title'])}字です（60字以内）")
        if len(d.get("text", "")) > LIMITS["news.text"]:
            probs.append(f"text が{len(d['text'])}字です（160字以内）")
        if d.get("date") and not re.fullmatch(r"20\d\d\.\d\d\.\d\d", d["date"]):
            probs.append("date は YYYY.MM.DD 形式にしてください")
        return probs

    user = f"""今日の日付（日本時間）: {TODAY}

# 記事
タイトル: {c['title']}
媒体: {c.get('source','')}
公開日: {c.get('date') or '不明'}
本文（抜粋）:
{source_text}

# タスク
この記事を、サイトのニュース欄用に日本語でまとめてください。
- title: 60字以内。「誰が・何を」が一目でわかる見出し。必要なら「 — 」で補足を続ける
- text: 120〜160字の要約。です・だ調ミックスの簡潔なニュース文体
- date: 記事の公開日（YYYY.MM.DD）。本文から分からなければ空文字
- 本文に書かれていない数字・固有名詞は使わない
- マイクロモビリティと無関係、または本文から内容が読み取れない場合は {{"skip": true}}

# 出力形式
{{"title": "...", "text": "...", "date": "YYYY.MM.DD"}}"""
    d = llm.json(SYSTEM, user, check=check, max_tokens=1200)
    if d.get("skip"):
        log(f"  LLMが見送り: {c['title'][:50]}")
        return None
    date = c.get("date") or parse_date(d.get("date"))
    if not date or not is_recent(date):
        log(f"  日付不明・古いため見送り: {c['title'][:50]}")
        return None
    return {
        "region": c["region"],
        "date": date,
        "title": clip(d["title"], LIMITS["news.title"]),
        "text": clip(d["text"], LIMITS["news.text"]),
        "url": c["url"],
    }


# ---- ステップ3: ティッカー・地域パルス・注目・ランキング --------------------
def step_headlines(llm: LLM, data: dict) -> None:
    news_lines = "\n".join(
        f"[{i}] ({n['region']}) {n['date']} {n['title']} — {n['text']}" for i, n in enumerate(data["news"])
    )
    pulse_now = "\n".join(f"- {p['cls']}（{p['ja']}）: {p['stat']} / {p['text']}" for p in data["pulse"])
    rank_now = "\n".join(
        f"- {r['name']}（{r['meta']}）score={r['score']} tags={r['tags']}\n    {r['desc']}" for r in data["ranking"]
    )
    feat = data["featured"]
    names = {r["name"] for r in data["ranking"]}

    def check(d):
        probs = []
        t = d.get("ticker")
        if not isinstance(t, list) or not (5 <= len(t) <= 7):
            probs.append("ticker は6本の配列にしてください")
        else:
            probs += [f"ticker「{x[:12]}…」が{len(x)}字です（35字以内）" for x in t if len(x) > 36]
        pl = d.get("pulse", {})
        for r in PULSE_REGIONS:
            p = pl.get(r) if isinstance(pl, dict) else None
            if not p or not p.get("stat") or not p.get("text"):
                probs.append(f"pulse.{r} がありません")
                continue
            if len(p["stat"]) > LIMITS["pulse.stat"]:
                probs.append(f"pulse.{r}.stat が{len(p['stat'])}字です（20字以内）")
            if len(p["text"]) > 120:
                probs.append(f"pulse.{r}.text が{len(p['text'])}字です（120字以内）")
        f = d.get("featured", -1)
        if not isinstance(f, int) or not (-1 <= f < len(data["news"])):
            probs.append("featured はニュース番号か -1 にしてください")
        for r in d.get("ranking", []) or []:
            if r.get("name") not in names:
                probs.append(f"ranking に存在しない名前: {r.get('name')}")
            elif len(r.get("desc", "")) > 110:
                probs.append(f"ranking[{r['name']}].desc が{len(r['desc'])}字です（110字以内）")
        return probs

    user = f"""今日の日付（日本時間）: {TODAY}

# 現在のニュース一覧（番号つき）
{news_lines}

# 現在の地域パルス
{pulse_now}

# 現在の注目ニュース（featured）
{feat['date']} {feat['title']}

# 現在のランキング
{rank_now}

# タスク
ニュース一覧だけを根拠に、サイト上部の要約パーツを更新してください。
1. ticker: ニュースの中から特に目を引く6本を、絵文字1つ（国旗など）＋短い一文（35字以内）で
2. pulse: jp/na/eu/as 各地域の「今いちばんの動き」。stat は20字以内の見出し、text は120字以内。
   既存文への追記ではなく、新旧の話題から重要な2〜3点だけを選んで毎回ゼロから書き直す。その地域の新しいニュースがなければ現状の内容を短く整える
3. featured: 現在の注目より明らかに大きいニュースがあればその番号、なければ -1
4. ranking: ニュースに大きな動き（業績発表・撤退・新規参入など）が出てきたプレイヤーだけ、desc（110字以内・追記せず書き直し）・tags（最大3個・各12字以内）・score（0〜100、変動は±3程度まで）を出す。動きがなければ空配列。name は現在のランキングと完全に同じ表記で
ニュースに書かれていない数字や出来事を足さないこと。

# 出力形式
{{"ticker": ["🇯🇵 ...", "..."],
 "pulse": {{"jp": {{"stat": "...", "text": "..."}}, "na": {{...}}, "eu": {{...}}, "as": {{...}}}},
 "featured": -1,
 "ranking": [{{"name": "Lime", "desc": "...", "tags": ["..."], "score": 97}}]}}"""
    d = llm.json(SYSTEM, user, check=check, max_tokens=4000)

    if isinstance(d.get("ticker"), list) and 3 <= len(d["ticker"]) <= 8:
        data["ticker"] = [str(t) for t in d["ticker"]]
    for p in data["pulse"]:
        new = (d.get("pulse") or {}).get(p["cls"])
        if isinstance(new, dict) and new.get("stat") and new.get("text"):
            p["stat"], p["text"] = str(new["stat"]), str(new["text"])
    f = d.get("featured", -1)
    if isinstance(f, int) and 0 <= f < len(data["news"]):
        n = data["news"][f]
        data["featured"] = {k: n[k] for k in ("date", "title", "text", "url")}
    by_name = {r["name"]: r for r in data["ranking"]}
    for upd in d.get("ranking", []) or []:
        r = by_name.get(upd.get("name"))
        if not r:
            continue
        if upd.get("desc"):
            r["desc"] = str(upd["desc"])
        if isinstance(upd.get("tags"), list) and upd["tags"]:
            r["tags"] = [str(t) for t in upd["tags"]][:3]
        if isinstance(upd.get("score"), (int, float)):
            # ローカルLLMの暴走対策：1日の変動は±3まで
            r["score"] = int(max(r["score"] - 3, min(r["score"] + 3, upd["score"])))


# ---------------------------------------------------------------------------
# 検証・文字数制限（以前の版と同じルール）
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="data.jsonを書き換えずに結果を表示")
    ap.add_argument("--candidates-file", help="検索の代わりに使う候補JSON（テスト用）")
    args = ap.parse_args()

    current = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    data = json.loads(json.dumps(current))  # 深いコピー
    llm = LLM()

    log("1/4 ニュース候補を収集中...")
    if args.candidates_file:
        rows = json.loads(Path(args.candidates_file).read_text(encoding="utf-8"))
        cands = dedupe_candidates(rows, current)
    else:
        cands = collect_candidates(current)
    log(f"  新しい候補 {len(cands)} 件")

    new_items = []
    if cands:
        log("2/4 LLMが採用記事を選定中...")
        picked = step_select(llm, current, cands)
        log(f"  採用候補 {len(picked)} 件")
        if picked and not args.candidates_file:
            with futures.ThreadPoolExecutor(max_workers=6) as ex:
                picked = list(ex.map(fetch_article, picked))
        log("3/4 LLMが1本ずつ要約中...")
        for c in picked:
            try:
                item = step_summarize(llm, c)
            except Exception as e:
                log(f"  要約失敗（見送り）: {e}")
                item = None
            if item:
                new_items.append(item)
                log(f"  + [{item['region']}] {item['title']}")

    if new_items:
        merged = new_items + data["news"]
        merged.sort(key=lambda n: n["date"], reverse=True)
        data["news"] = merged[:NEWS_KEEP]

    log("4/4 LLMがティッカー・地域パルス・ランキングを更新中...")
    try:
        step_headlines(llm, data)
    except Exception as e:
        log(f"  見出し更新に失敗（ニュース追加分のみ反映）: {e}")

    # 既存のアーカイブは残し、新しい年月を足す
    data["archive"] = sorted(set(data["archive"]) | {n["date"][:7] for n in data["news"]}, reverse=True)
    data["updated"] = TODAY
    enforce_limits(data)

    try:
        validate(data)
    except ValueError as e:
        log(f"ERROR: 検証に失敗したため更新しません: {e}")
        return 1

    if data == current:
        print("変更なし。data.jsonは更新しませんでした。")
        return 0
    out = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(out)
        return 0
    DATA_PATH.write_text(out, encoding="utf-8")
    print(f"data.json を更新しました（新規 {len(new_items)} 件 / 計 {len(data['news'])} 件 / {TODAY}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
