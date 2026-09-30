"""
Amazon US TV OS 데이터 수집기

- 데이터 소스: Rainforest API (https://www.rainforestapi.com) — 무료 체험 키로 시작 가능
  다른 상용 API(SerpApi, Bright Data 등)를 쓰려면 fetch_rainforest()와 같은 형태의 함수를 하나 더 만들고
  PROVIDERS 딕셔너리에 등록하면 됩니다.
- 출력: data/index.json (마켓 목록), data/<마켓>/tv-os.json (스냅샷), data/<마켓>/history.json (이력)
  첫 마켓은 data/tv-os.json 에도 복사됩니다 (구버전 대시보드 호환)
- API 키가 없으면 data/sample-products-<마켓>.json 을 읽어 파이프라인만 검증합니다.
- 마켓 선택: 환경변수 MARKETPLACES="us,uk" (기본값)
"""
import json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

DEFAULT_TERMS = [
    "smart tv",
    "55 inch smart tv",
    "65 inch smart tv",
    "75 inch 4k tv",
    "oled tv",
    "qled tv",
    "tizen smart tv",   # 라이선스 브랜드(RCA, Bauhn, Linsar 등) 노출용
]
SEARCH_TERMS = [t.strip() for t in os.getenv("SEARCH_TERMS", "").split(",") if t.strip()] or DEFAULT_TERMS
PAGES_PER_TERM = int(os.getenv("PAGES_PER_TERM") or 1)

# 수집할 아마존 마켓. 쉼표 구분 코드. 예: us,uk,de
MARKETS = {
    "us": {"domain": "amazon.com",    "label": "Amazon US", "currency": "USD", "symbol": "$"},
    "uk": {"domain": "amazon.co.uk",  "label": "Amazon UK", "currency": "GBP", "symbol": "£"},
    "de": {"domain": "amazon.de",     "label": "Amazon DE", "currency": "EUR", "symbol": "€"},
    "fr": {"domain": "amazon.fr",     "label": "Amazon FR", "currency": "EUR", "symbol": "€"},
    "ca": {"domain": "amazon.ca",     "label": "Amazon CA", "currency": "CAD", "symbol": "C$"},
    "jp": {"domain": "amazon.co.jp",  "label": "Amazon JP", "currency": "JPY", "symbol": "¥"},
    "in": {"domain": "amazon.in",     "label": "Amazon IN", "currency": "INR", "symbol": "₹"},
}
MARKET_CODES = [m.strip().lower() for m in (os.getenv("MARKETPLACES") or "us,uk").split(",") if m.strip()]

# 아마존 자체 "운영체제" 필터가 적용된 검색 URL. 여기서 나온 제품은 제품명과 상관없이 해당 OS로 확정 분류.
# 아마존 검색 페이지에서 OS 필터를 건 뒤 주소를 복사해 넣으면 됩니다 (qid, crid 같은 세션 값은 지워도 됨).
# 저장소 Variables 에 FILTER_URLS 를 JSON 으로 넣으면 아래 기본값 대신 사용됩니다.
DEFAULT_FILTER_URLS = {
    "uk": [
        {"os": "Tizen", "pages": 2,
         "url": "https://www.amazon.co.uk/s?k=tv&rh=p_n_g-101016028402111%3A205632524031&dc"},
    ],
}
try:
    FILTER_URLS = json.loads(os.getenv("FILTER_URLS") or "null") or DEFAULT_FILTER_URLS
except json.JSONDecodeError:
    print("FILTER_URLS 변수가 올바른 JSON이 아닙니다. 기본값을 사용합니다.", file=sys.stderr)
    FILTER_URLS = DEFAULT_FILTER_URLS

# ---------- OS 분류 ----------
# (정규식, OS명) — 위에서부터 먼저 매칭되는 것을 채택. 제목/스펙 텍스트 기준.
OS_PATTERNS = [
    (r"\btizen\b", "Tizen"),
    (r"\bwebos\b", "webOS"),
    (r"\bfire\s*tv\b|\bfire\s*os\b", "Fire TV"),
    (r"\broku\b", "Roku TV"),
    (r"\bgoogle\s*tv\b", "Google TV"),
    (r"\bandroid\s*tv\b", "Android TV"),
    (r"\bvidaa\b", "VIDAA"),
    (r"\bsmartcast\b", "SmartCast"),
    (r"\bxumo\b", "Xumo TV"),
    (r"\bwhale\s*os\b", "WhaleOS"),
    (r"\btitan\s*os\b", "Titan OS"),
]
# 텍스트에 OS 단서가 없을 때 브랜드로 추정 (추정값은 os_inferred=True 로 표시)
BRAND_DEFAULT_OS = {
    "samsung": "Tizen",
    "lg": "webOS",
    "vizio": "SmartCast",
    "sony": "Google TV",
    "amazon": "Fire TV",
    "insignia": "Fire TV",
    "roku": "Roku TV",
}
# Tizen 라이선스 브랜드 (Samsung 외). 대시보드에서 "파트너 브랜드"로 집계
TIZEN_PARTNER_BRANDS = {"rca", "akai", "bauhn", "linsar", "vispera", "sunny", "axen", "eko", "qbell", "axdia",
                        "hye", "cello", "tempo", "hkc", "atmaca"}
BRAND_PATTERN = re.compile(
    r"\b(samsung|lg|sony|tcl|hisense|vizio|amazon|insignia|toshiba|roku|philips|panasonic|sharp|westinghouse|onn|element"
    r"|skyworth|xiaomi|jvc|bush|loewe|grundig|telefunken|thomson|metz|nokia|medion|continental edison|polaroid|cello"
    r"|rca|akai|bauhn|linsar|vispera|sunny|axen|eko|qbell|axdia|konka|aiwa|hyundai|schneider|blaupunkt|kogan|chiq|haier|hye|cello|ferguson|mitchell & brown|avtex|veltech)\b",
    re.I,
)


def classify(title: str, brand: str, extra_text: str = ""):
    text = f"{title} {extra_text}".lower()
    for pat, name in OS_PATTERNS:
        if re.search(pat, text):
            return name, False
    b = (brand or "").lower()
    if not b:
        m = BRAND_PATTERN.search(title or "")
        b = m.group(1).lower() if m else ""
    if b in BRAND_DEFAULT_OS:
        return BRAND_DEFAULT_OS[b], True
    return "Unknown", False


def extract_size(title: str):
    m = re.search(r"(\d{2,3})\s*[-\"”']?\s*(inch|in\b|”|\")", title or "", re.I)
    return int(m.group(1)) if m else None


def detect_brand(title: str, brand_field: str):
    if brand_field:
        return brand_field.strip()
    m = BRAND_PATTERN.search(title or "")
    if m:
        b = m.group(1)
        return b.upper() if (len(b) <= 4 and b.lower() not in ("sony", "onn", "bush", "akai")) or b.lower() in ("qbell", "eko") else b.title()
    # 목록에 없는 브랜드: 제품명 첫 단어를 브랜드로 사용
    first = re.match(r"^[A-Za-z][A-Za-z0-9&.-]*", (title or "").strip())
    return first.group(0) if first else ""


# ---------- 데이터 소스 ----------
def fetch_rainforest(domain: str, term: str, page: int, url: str = None):
    key = os.environ["RAINFOREST_API_KEY"]
    params = {"api_key": key, "type": "search", "page": page, "output": "json"}
    if url:
        params["url"] = url          # 아마존 검색 URL 그대로 (필터 포함)
    else:
        params.update({"amazon_domain": domain, "search_term": term, "category_id": "aps"})
    r = requests.get("https://api.rainforestapi.com/request", params=params, timeout=60)
    r.raise_for_status()
    body = r.json()
    items = []
    for it in body.get("search_results", []):
        if it.get("sponsored"):
            continue
        price = (it.get("price") or {}).get("value")
        items.append({
            "asin": it.get("asin"),
            "title": it.get("title", ""),
            "brand": it.get("brand", ""),
            "price": price,
            "rating": it.get("rating"),
            "ratings_total": it.get("ratings_total"),
            "position": it.get("position"),
            "url": it.get("link"),
            "image": it.get("image"),
            "is_prime": bool(it.get("is_prime")),
            "bestseller": bool(it.get("bestseller")),
        })
    return items


PROVIDERS = {
    "rainforest": ("RAINFOREST_API_KEY", fetch_rainforest),
}


def pick_provider():
    for name, (env, fn) in PROVIDERS.items():
        if os.getenv(env):
            return name, fn
    return None, None


# ---------- 메인 ----------
def collect_market(code: str, provider, fetch):
    mk = MARKETS[code]
    out_dir = DATA / code
    out_dir.mkdir(exist_ok=True)
    raw = []
    src = provider
    if provider and requests:
        for term in SEARCH_TERMS:
            for page in range(1, PAGES_PER_TERM + 1):
                try:
                    items = fetch(mk["domain"], term, page)
                    for it in items:
                        it["query"] = term
                    raw.extend(items)
                    print(f"[{code}/{provider}] {term!r} p{page}: {len(items)}개")
                    time.sleep(1)
                except Exception as e:
                    print(f"[{code}/{provider}] {term!r} p{page} 실패: {e}", file=sys.stderr)
        for f in FILTER_URLS.get(code, []):
            for page in range(1, int(f.get("pages", 1)) + 1):
                try:
                    items = fetch(mk["domain"], None, page, url=f["url"])
                    for it in items:
                        it["query"] = f"[{f['os']} 필터]"
                        it["os_forced"] = f["os"]
                    raw.extend(items)
                    print(f"[{code}/{provider}] {f['os']} 필터 URL p{page}: {len(items)}개")
                    time.sleep(1)
                except Exception as e:
                    print(f"[{code}/{provider}] 필터 URL p{page} 실패: {e}", file=sys.stderr)
    else:
        sample = DATA / f"sample-products-{code}.json"
        if not sample.exists():
            sample = DATA / "sample-products.json" if code == "us" else sample
        if not sample.exists():
            print(f"[{code}] API 키가 없고 샘플 파일({sample.name})도 없습니다. 건너뜁니다.", file=sys.stderr)
            return None
        src = "sample"
        raw = json.loads(sample.read_text(encoding="utf-8"))
        print(f"[{code}] API 키 없음 → 샘플 데이터 {len(raw)}개 사용")

    seen, products = set(), []
    raw.sort(key=lambda x: 0 if x.get("os_forced") else 1)   # 필터 결과 우선
    for it in raw:
        asin = it.get("asin")
        if not asin or asin in seen:
            continue
        seen.add(asin)
        brand = detect_brand(it.get("title", ""), it.get("brand", ""))
        os_name, inferred = classify(it.get("title", ""), brand)
        if it.get("os_forced"):
            os_name, inferred = it["os_forced"], False
        # 필터 URL 결과가 일반 검색 결과와 겹치면 필터 쪽 분류를 우선
        products.append({**it, "brand": brand, "os": os_name, "os_inferred": inferred,
                         "tizen_partner": os_name == "Tizen" and brand.lower() != "samsung",
                         "size": extract_size(it.get("title", ""))})

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_os = {}
    for p in products:
        d = by_os.setdefault(p["os"], {"count": 0, "prices": []})
        d["count"] += 1
        if p.get("price"):
            d["prices"].append(p["price"])
    summary = {k: {"count": d["count"],
                   "avg_price": round(sum(d["prices"]) / len(d["prices"]), 2) if d["prices"] else None}
               for k, d in by_os.items()}

    snapshot = {"generated_at": now, "source": src, "market": code, "marketplace": mk["domain"],
                "label": mk["label"], "currency": mk["currency"], "symbol": mk["symbol"],
                "queries": SEARCH_TERMS, "summary": summary, "products": products}
    (out_dir / "tv-os.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    hist_path = out_dir / "history.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    day = now[:10]
    entry = {"date": day, "generated_at": now, "total": len(products),
             "os_counts": {k: v["count"] for k, v in summary.items()},
             "avg_price": {k: v["avg_price"] for k, v in summary.items()}}
    history = ([h for h in history if h["date"] != day] + [entry])[-365:]
    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    # 첫 번째 마켓(보통 US)은 예전 경로에도 복사해 기존 대시보드와 호환
    if code == MARKET_CODES[0]:
        (DATA / "tv-os.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        (DATA / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{code}] 완료: 제품 {len(products)}개, OS 분포 {entry['os_counts']}")
    return {"code": code, "label": mk["label"], "domain": mk["domain"], "currency": mk["currency"],
            "symbol": mk["symbol"], "generated_at": now, "total": len(products)}


def main():
    provider, fetch = pick_provider()
    unknown = [c for c in MARKET_CODES if c not in MARKETS]
    if unknown:
        print(f"알 수 없는 마켓 코드: {unknown}. 사용 가능: {list(MARKETS)}", file=sys.stderr)
        sys.exit(1)
    print(f"마켓: {MARKET_CODES}, 데이터 소스: {provider or '샘플 파일'}, 검색어 {len(SEARCH_TERMS)}개")
    results = [r for r in (collect_market(c, provider, fetch) for c in MARKET_CODES) if r]
    if not results:
        print("수집된 마켓이 없습니다. RAINFOREST_API_KEY 를 등록하거나 data/sample-products-<마켓>.json 을 추가하세요.", file=sys.stderr)
        sys.exit(1)
    (DATA / "index.json").write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                 "markets": results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
