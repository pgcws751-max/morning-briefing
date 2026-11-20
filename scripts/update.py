"""아침 브리핑 데이터 갱신 스크립트 (GitHub Actions에서 매일 실행).

data/ 아래 JSON 파일 4개를 새로 만듭니다.
- weather.json   : Open-Meteo (안산시 상록구 건건동)
- markets.json   : 네이버 증권 (안 되면 Stooq, Yahoo Finance로 대체)
- history.json   : 최근 6거래일 종가 (차트용)
- headlines.json : 외신 RSS. ANTHROPIC_API_KEY가 있으면 Claude가 골라서 우리말로 옮기고
                   분야별로 나눕니다. 없으면 영어 제목 그대로 언론사별로 보여 줍니다.

한 부분이 실패해도 나머지는 갱신하고, 실패한 파일은 이전 내용을 그대로 둡니다.
"""

from __future__ import annotations

import email.utils
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DATA = Path(__file__).resolve().parent.parent / "data"
NOW = datetime.now(KST)
STAMP = NOW.strftime("%Y-%m-%dT%H:%M+09:00")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

PLACE = "안산 건건동"
LAT, LON = 37.306, 126.907
HISTORY_DAYS = 6


def get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url: str):
    return json.loads(get(url))


def write(name: str, rows: list, source: str) -> None:
    out = {"rows": rows, "meta": [{"label": "출처", "value": source}, {"label": "수집 시각", "value": STAMP}]}
    (DATA / f"{name}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[ok] {name}.json ({len(rows)} rows)")


# ---------------------------------------------------------------- 날씨

SKY = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림", 45: "안개", 48: "안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 56: "어는 이슬비", 57: "어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "어는 비", 67: "어는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기", 85: "눈보라", 86: "강한 눈보라",
    95: "뇌우", 96: "우박 동반 뇌우", 99: "우박 동반 뇌우",
}


def weather() -> None:
    d = get_json(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code"
        "&daily=temperature_2m_min,temperature_2m_max,precipitation_probability_max,weather_code"
        "&timezone=Asia%2FSeoul&forecast_days=1"
    )
    c, day = d["current"], d["daily"]
    row = {
        "place": PLACE,
        "time": c["time"],
        "temp": round(c["temperature_2m"], 1),
        "feels": round(c["apparent_temperature"], 1),
        "sky": SKY.get(c["weather_code"], "알 수 없음"),
        "humidity": c["relative_humidity_2m"],
        "wind": round(c["wind_speed_10m"], 1),
        "min": round(day["temperature_2m_min"][0], 1),
        "max": round(day["temperature_2m_max"][0], 1),
        "day_sky": SKY.get(day["weather_code"][0], "알 수 없음"),
        "rain_pct": day["precipitation_probability_max"][0] or 0,
    }
    write("weather", [row], f"Open-Meteo 예보 (안산시 상록구 건건동, 위도 {LAT}, 경도 {LON})")


# ---------------------------------------------------------------- 증시

def num(v) -> float:
    return float(str(v).replace(",", ""))


def naver_index(code: str) -> list[tuple[str, float]]:
    """네이버 증권 국내 지수 일별 종가. [(YYYY-MM-DD, 종가), ...] 오래된 순."""
    return _naver(f"https://m.stock.naver.com/api/index/{code}/price?pageSize=10&page=1")


def naver_world(kind: str, code: str) -> list[tuple[str, float]]:
    """네이버 증권 해외 지수(kind=index)·종목(kind=stock) 일별 종가."""
    return _naver(f"https://api.stock.naver.com/{kind}/{code}/price?pageSize=10&page=1")


def _naver(url: str) -> list[tuple[str, float]]:
    d = get_json(url)
    if isinstance(d, dict):
        d = d.get("result") or d.get("priceInfos") or d.get("items") or []
    return sorted((str(x["localTradedAt"])[:10], num(x["closePrice"])) for x in d)


def stooq(symbol: str) -> list[tuple[str, float]]:
    """Stooq 일별 종가 CSV (Date,Open,High,Low,Close,Volume)."""
    start = (NOW - timedelta(days=30)).strftime("%Y%m%d")
    text = get(f"https://stooq.com/q/d/l/?s={urllib.request.quote(symbol)}&d1={start}&i=d").decode()
    pts = []
    for line in text.strip().splitlines()[1:]:
        f = line.split(",")
        if len(f) >= 5 and f[4] not in ("", "null"):
            pts.append((f[0], round(float(f[4]), 2)))
    return sorted(pts)


def yahoo(symbol: str) -> list[tuple[str, float]]:
    """Yahoo Finance 일별 종가 (마지막 대체 수단)."""
    sym = urllib.request.quote(symbol)
    d = get_json(f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d")
    res = d["chart"]["result"][0]
    off = res["meta"].get("gmtoffset", 0)
    closes = res["indicators"]["quote"][0]["close"]
    pts = [(datetime.fromtimestamp(ts + off, timezone.utc).strftime("%Y-%m-%d"), round(cl, 2))
           for ts, cl in zip(res["timestamp"], closes) if cl is not None]
    return pts


def first_ok(name: str, *tries):
    """모든 출처를 확인해 가장 최근 종가가 있는 것을 고릅니다 (날짜가 같으면 앞 순서 우선).
    네이버가 가끔 하루 늦게 갱신되는 지수가 있어서입니다."""
    errors, best = [], None
    for label, fn in tries:
        try:
            pts = fn()
            if len(pts) < 2:
                errors.append(f"{label}: 데이터 {len(pts)}개")
            elif best is None or pts[-1][0] > best[0][-1][0]:
                best = (pts, label)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {e}")
    if errors:
        print(f"[info] {name} 일부 출처 실패: {'; '.join(errors)}")
    if best is None:
        raise RuntimeError(f"{name} 전부 실패")
    return best


MARKETS = [
    # (구분, 이름, 단위, 시도 순서, 차트에 넣을지)
    ("미국", "S&P 500", "pt", [("네이버", lambda: naver_world("index", ".INX")), ("Stooq", lambda: stooq("^spx")), ("Yahoo", lambda: yahoo("^GSPC"))], True),
    ("미국", "다우존스", "pt", [("네이버", lambda: naver_world("index", ".DJI")), ("Stooq", lambda: stooq("^dji")), ("Yahoo", lambda: yahoo("^DJI"))], False),
    ("미국", "나스닥", "pt", [("네이버", lambda: naver_world("index", ".IXIC")), ("Stooq", lambda: stooq("^ndq")), ("Yahoo", lambda: yahoo("^IXIC"))], False),
    ("한국", "코스피", "pt", [("네이버", lambda: naver_index("KOSPI")), ("Yahoo", lambda: yahoo("^KS11"))], True),
    ("한국", "코스닥", "pt", [("네이버", lambda: naver_index("KOSDAQ")), ("Yahoo", lambda: yahoo("^KQ11"))], True),
    ("종목", "엔비디아", "USD", [("네이버", lambda: naver_world("stock", "NVDA.O")), ("Stooq", lambda: stooq("nvda.us")), ("Yahoo", lambda: yahoo("NVDA"))], True),
]


def markets() -> None:
    rows, hist = [], []
    used = set()
    for market, name, unit, tries, chart in MARKETS:
        try:
            pts, label = first_ok(name, *tries)
            used.add(label)
            print(f"[info] {name}: {label} ({pts[-1][0]} 종가 {pts[-1][1]})")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {e}")
            continue
        (d0, prev), (d1, close) = pts[-2], pts[-1]
        rows.append({
            "market": market, "name": name, "date": d1, "close": round(close, 2),
            "change": round(close - prev, 2), "pct": round((close / prev - 1) * 100, 2), "unit": unit,
        })
        if chart:
            hist += [{"name": name, "date": d, "close": round(c, 2)} for d, c in pts[-HISTORY_DAYS:]]
    if len(rows) < len(MARKETS) // 2:
        raise RuntimeError(f"증시 데이터를 {len(rows)}개밖에 못 받았습니다")
    src = "네이버 증권" if used == {"네이버"} else ", ".join(sorted(used))
    write("markets", rows, f"{src} (장 마감 기준)")
    order = ["코스피", "코스닥", "S&P 500", "엔비디아"]
    hist.sort(key=lambda r: (order.index(r["name"]), r["date"]))
    write("history", hist, f"{src} 일별 종가")


# ---------------------------------------------------------------- 헤드라인

FEEDS = [
    ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("The Guardian", "https://www.theguardian.com/world/rss"),
    ("The Guardian", "https://www.theguardian.com/business/rss"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
]
MAX_AGE_H = 30
PER_FEED = 25


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(KST)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(KST)
        except ValueError:
            return None


def collect() -> list[dict]:
    items, seen = [], set()
    cutoff = NOW - timedelta(hours=MAX_AGE_H)
    for source, url in FEEDS:
        try:
            root = ET.fromstring(get(url))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] RSS 실패 {source} {url}: {e}")
            continue
        n = 0
        for it in root.iter("item"):
            title = re.sub(r"\s+", " ", (it.findtext("title") or "")).strip()
            link = (it.findtext("link") or "").strip()
            when = parse_date(it.findtext("pubDate"))
            if not title or not link.startswith("https://") or link in seen:
                continue
            if when and when < cutoff:
                continue
            seen.add(link)
            items.append({
                "id": len(items), "source": source, "title_en": title, "url": link,
                "published": (when or NOW).strftime("%Y-%m-%dT%H:%M+09:00"),
            })
            n += 1
            if n >= PER_FEED:
                break
    print(f"[info] 헤드라인 후보 {len(items)}건")
    return items


SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["id", "category", "title"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

PROMPT = """아래는 오늘 아침 외신 RSS에서 모은 영어 헤드라인 후보입니다. 한국에 사는 직장인이 출근 전에 훑어볼 아침 브리핑을 만들려고 합니다.

할 일:
1. 중요한 기사를 30~36건 고르세요. 같은 사건을 다룬 기사가 여러 개면 가장 정보가 많은 것 위주로 남기고, 한 사건에 너무 많이 몰리지 않게 하세요. 연예·스포츠 가십, 생활 정보, 오피니언 칼럼은 빼세요.
2. 각 제목을 자연스러운 한국 신문 헤드라인 문체로 옮기세요. 사람 이름과 기관명은 한국 언론에서 흔히 쓰는 표기를 따르세요.
3. 분야를 붙이세요. 기본 분야는 "중동", "러시아·우크라이나", "경제·시장", "한반도", "국제"입니다. 오늘 특히 큰 사건이 있어 관련 기사가 4건 이상이면 "APEC 정상회의"처럼 그 사건 이름으로 분야를 따로 만들어도 됩니다.
4. 결과는 분야별로 묶고(가장 중요한 분야 먼저), 분야 안에서는 최신 기사부터 나열하세요. id는 후보 목록의 번호를 그대로 쓰세요.

후보 (id | 언론사 | 게시 시각 | 제목):
"""


def translate(items: list[dict]) -> list[dict] | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[info] ANTHROPIC_API_KEY가 없어 번역을 건너뜁니다")
        return None
    import anthropic

    lines = "\n".join(f'{x["id"]} | {x["source"]} | {x["published"][5:16]} | {x["title_en"]}' for x in items)
    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SELECT_SCHEMA}},
        messages=[{"role": "user", "content": PROMPT + lines}],
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        print(f"[warn] Claude 응답 중단: {resp.stop_reason}")
        return None
    text = next(b.text for b in resp.content if b.type == "text")
    picked = json.loads(text)["items"]
    by_id = {x["id"]: x for x in items}
    rows, used = [], set()
    for p in picked:
        x = by_id.get(p["id"])
        if not x or p["id"] in used:
            continue
        used.add(p["id"])
        rows.append({
            "category": p["category"].strip() or "국제", "title": p["title"].strip(),
            "title_en": x["title_en"], "source": x["source"], "url": x["url"], "published": x["published"],
        })
    print(f"[info] Claude가 {len(rows)}건 선택·번역 (입력 {resp.usage.input_tokens}, 출력 {resp.usage.output_tokens} 토큰)")
    return rows or None


def headlines() -> None:
    items = collect()
    if len(items) < 10:
        raise RuntimeError(f"헤드라인 후보가 {len(items)}건뿐입니다")
    try:
        rows = translate(items)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 번역 실패, 영어 제목으로 대체: {e}")
        rows = None
    if rows:
        write("headlines", rows, "BBC, New York Times, The Guardian, Al Jazeera, CNBC RSS (제목은 우리말 번역)")
        return
    # 번역 없이: 언론사별 최신 6건
    rows, count = [], {}
    for x in sorted(items, key=lambda x: x["published"], reverse=True):
        if count.get(x["source"], 0) >= 6:
            continue
        count[x["source"]] = count.get(x["source"], 0) + 1
        rows.append({"category": x["source"], "title": x["title_en"], "title_en": "",
                     "source": x["source"], "url": x["url"], "published": x["published"]})
    rows.sort(key=lambda r: [s for s, _ in FEEDS].index(r["source"]))
    write("headlines", rows, "BBC, New York Times, The Guardian, Al Jazeera, CNBC RSS (영어 원문)")


# ----------------------------------------------------------------

def main() -> int:
    failed = []
    for name, fn in [("weather", weather), ("markets", markets), ("headlines", headlines)]:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"[error] {name} 갱신 실패, 이전 데이터를 유지합니다: {e!r}")
    if len(failed) == 3:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
