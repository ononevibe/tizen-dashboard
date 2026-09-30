"""
Amazon US TV OS 데이터 수집기

- 데이터 소스: Rainforest API (https://www.rainforestapi.com) — 무료 체험 키로 시작 가능
  다른 상용 API(SerpApi, Bright Data 등)를 쓰려면 fetch_rainforest()와 같은 형태의 함수를 하나 더 만들고
  PROVIDERS 딕셔너리에 등록하면 됩니다.
- 출력: data/tv-os.json (최신 스냅샷), data/history.json (일자별 OS 집계 이력)
- API 키가 없으면 data/sample-products.json 을 읽어 파이프라인만 검증합니다.
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
]
SEARCH_TERMS = [t.strip() for t in os.getenv("SEARCH_TERMS", "").split(",") if t.strip()] or DEFAULT_TERMS
PAGES_PER_TERM = int(os.getenv("PAGES_PER_TERM") or 1)
MARKETPLACE = "amazon.com"

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
BRAND_PATTERN = re.compile(
    r"\b(samsung|lg|sony|tcl|hisense|vizio|amazon|insignia|toshiba|roku|philips|panasonic|sharp|westinghouse|onn|element|skyworth|xiaomi)\b",
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
    return m.group(1).upper() if m and len(m.group(1)) <= 3 else (m.group(1).title() if m else "")


# ---------- 데이터 소스 ----------
def fetch_rainforest(term: str, page: int):
    key = os.environ["RAINFOREST_API_KEY"]
    params = {
        "api_key": key,
        "type": "search",
        "amazon_domain": MARKETPLACE,
        "search_term": term,
        "category_id": "aps",
        "page": page,
        "output": "json",
    }
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
def main():
    provider, fetch = pick_provider()
    raw = []
    if provider and requests:
        for term in SEARCH_TERMS:
            for page in range(1, PAGES_PER_TERM + 1):
                try:
                    items = fetch(term, page)
                    for it in items:
                        it["query"] = term
                    raw.extend(items)
                    print(f"[{provider}] {term!r} p{page}: {len(items)}개")
                    time.sleep(1)
                except Exception as e:
                    print(f"[{provider}] {term!r} p{page} 실패: {e}", file=sys.stderr)
    else:
        sample = DATA / "sample-products.json"
        if not sample.exists():
            print("API 키가 없고 샘플 파일도 없습니다. RAINFOREST_API_KEY 를 설정하세요.", file=sys.stderr)
            sys.exit(1)
        provider = "sample"
        raw = json.loads(sample.read_text(encoding="utf-8"))
        print(f"API 키 없음 → 샘플 데이터 {len(raw)}개 사용")

    # ASIN 기준 중복 제거 (가장 앞 순위만 유지)
    seen, products = set(), []
    for it in raw:
        asin = it.get("asin")
        if not asin or asin in seen:
            continue
        seen.add(asin)
        brand = detect_brand(it.get("title", ""), it.get("brand", ""))
        os_name, inferred = classify(it.get("title", ""), brand)
        products.append({
            **it,
            "brand": brand,
            "os": os_name,
            "os_inferred": inferred,
            "size": extract_size(it.get("title", "")),
        })

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 집계
    by_os = {}
    for p in products:
        d = by_os.setdefault(p["os"], {"count": 0, "prices": []})
        d["count"] += 1
        if p.get("price"):
            d["prices"].append(p["price"])
    summary = {
        os_name: {
            "count": d["count"],
            "avg_price": round(sum(d["prices"]) / len(d["prices"]), 2) if d["prices"] else None,
        }
        for os_name, d in by_os.items()
    }

    snapshot = {
        "generated_at": now,
        "source": provider,
        "marketplace": MARKETPLACE,
        "queries": SEARCH_TERMS,
        "summary": summary,
        "products": products,
    }
    (DATA / "tv-os.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    # 이력: 하루에 한 항목(같은 날이면 덮어쓰기), 최대 365일 보관
    hist_path = DATA / "history.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    day = now[:10]
    entry = {"date": day, "generated_at": now, "total": len(products),
             "os_counts": {k: v["count"] for k, v in summary.items()},
             "avg_price": {k: v["avg_price"] for k, v in summary.items()}}
    history = [h for h in history if h["date"] != day] + [entry]
    history = history[-365:]
    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"완료: 제품 {len(products)}개, OS 분포 {entry['os_counts']}")


if __name__ == "__main__":
    main()
