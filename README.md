# Amazon TV OS 대시보드 (US · UK)

GitHub Actions가 6시간마다 Amazon US와 UK의 TV 검색 결과를 수집해 `data/<마켓>/tv-os.json`으로 저장하고,
`dashboard.html`이 그 파일을 읽어 OS(Tizen / webOS / Google TV / Fire TV / Roku / VIDAA 등)별 현황을 마켓 탭으로 보여줍니다.

```
.github/workflows/collect.yml     스케줄 실행 (6시간마다 + 수동)
scripts/collect.py                수집 → OS 분류 → JSON 저장
data/index.json                   수집된 마켓 목록 (대시보드 탭)
data/us/tv-os.json, history.json  Amazon US 스냅샷 + 이력
data/uk/tv-os.json, history.json  Amazon UK 스냅샷 + 이력
data/sample-products-us.json      API 키 없을 때 쓰는 샘플 (US)
data/sample-products-uk.json      API 키 없을 때 쓰는 샘플 (UK)
dashboard.html                    대시보드 (GitHub Pages로 배포 가능)
```

## 마켓 추가/변경

기본은 `us,uk`입니다. 저장소 Settings → Secrets and variables → Actions → **Variables** 탭에 `MARKETPLACES` 변수를
만들고 `us,uk,de` 처럼 쉼표로 적으면 됩니다. 지원 코드: `us` `uk` `de` `fr` `ca` `jp` `in`.
크레딧은 마켓 수만큼 곱해서 듭니다 (기본 6검색어 × 2마켓 × 하루 4회 = 48크레딧/일).

## 1. 바로 미리보기 (GitHub 없이)

`dashboard.html`을 브라우저로 열고 **파일 열기** → `data/us/tv-os.json` 또는 `data/uk/tv-os.json` 선택.

## 2. GitHub 저장소 만들기

1. GitHub에 **공개** 저장소를 만들고 이 폴더 내용을 그대로 push합니다.
2. Settings → Actions → General → Workflow permissions를 **Read and write**로 설정합니다. (데이터 파일을 커밋해야 하므로)
3. Actions 탭 → "Collect Amazon US TV OS data" → **Run workflow**로 한 번 실행해 `data/` 커밋이 생기는지 확인합니다.

## 3. 데이터 API 키 연결

기본 수집기는 [Rainforest API](https://www.rainforestapi.com)를 사용합니다. 무료 체험 크레딧으로 시작할 수 있고, 아마존 계정이나 Associates 가입은 필요 없습니다.

1. 가입 후 API 키 발급
2. 저장소 Settings → Secrets and variables → Actions → **New repository secret**
   - Name: `RAINFOREST_API_KEY`, Value: 발급받은 키
3. (선택) Variables 탭에서 검색어를 바꿀 수 있습니다.
   - `SEARCH_TERMS`: `smart tv,65 inch smart tv,oled tv` 처럼 쉼표 구분
   - `PAGES_PER_TERM`: 검색어당 페이지 수 (기본 1, 페이지당 약 20개 제품)

크레딧 사용량 = 검색어 수 × 페이지 수 × 마켓 수 × 하루 4회. 기본 설정(6개 × 1페이지 × 2마켓)이면 하루 48 크레딧입니다.

다른 데이터 서비스(SerpApi, Bright Data, Oxylabs 등)를 쓰려면 `scripts/collect.py`의 `fetch_rainforest()`와
같은 형태로 함수를 추가하고 `PROVIDERS`에 등록하면 됩니다.

## 4. 대시보드 연결

- `dashboard.html`을 열고 상단에 `owner/repo` 입력 → 연결. (다른 브랜치면 `owner/repo@branch`)
- 상단 탭으로 마켓을 전환하며, 주소에 `?m=uk`가 붙으므로 특정 마켓 화면을 바로 공유할 수 있습니다.
- github.io 주소로 열면 자기 저장소에 자동 연결됩니다.
- Settings → Pages에서 `main` 브랜치 루트를 배포하면 `https://owner.github.io/repo/dashboard.html?src=owner/repo` 로 어디서나 볼 수 있습니다.

## OS 분류 기준

`collect.py`의 `OS_PATTERNS`가 제품명에서 OS 키워드를 찾습니다. 제품명에 OS가 없으면 `BRAND_DEFAULT_OS`로 추정하고
대시보드에 **추정** 표시가 붙습니다 (예: Samsung → Tizen). 분류가 틀린 제품이 보이면 두 딕셔너리를 수정하세요.

## 참고

- 아마존 검색 결과는 노출 순위이므로 실제 판매량이 아니라 **검색 노출 기준 점유율**입니다. 판매 순위가 필요하면 Rainforest의 `type=bestsellers` 요청을 추가하는 방식으로 확장할 수 있습니다.
- 저장소가 공개여야 대시보드가 `raw.githubusercontent.com`에서 JSON을 읽을 수 있습니다. 비공개로 운영하려면 GitHub Pages(Private Pages, Enterprise) 또는 내부 서버에 JSON을 올리고 URL로 연결하세요.
