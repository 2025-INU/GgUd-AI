# GgUd-AI

장소 추천을 담당하는 FastAPI 서버. Spring Boot 백엔드가 호출해서 사용한다.

## 동작 요약

1. **오프라인 데이터 준비**
   - `crawl_near_stations.py` 가 지하철역 좌표 주변에서 음식점/카페 정보를 수집해 `places` 테이블에 적재
   - `fast_review_summarize.py` 가 각 장소의 네이버 리뷰를 가져와 GPT-4o-mini 로 요약 → 4개 카테고리(menu/companion/mood/purpose)로 추출 → text-embedding-3-small 로 임베딩 → `place_summary_embeddings` 에 저장
2. **온라인 추천**
   - Spring 이 `POST /recommend-places` 호출
   - 자연어 쿼리를 LLM 으로 분석해 카테고리·위치·업종 추출
   - 후보 장소를 위치/탭/업종/메뉴로 좁힌 뒤 카테고리별 코사인 유사도를 가중합 → 인기도(`review_count`) 보너스 적용 → 상위 N개 반환

## 구조

```
app/
├── main.py                      # FastAPI 진입점
├── api/
│   ├── routes.py
│   └── endpoints/
│       ├── spring_integration.py  # POST /recommend-places  ← 메인
│       ├── recommendations.py     # /api/v1/recommendations (디버깅용)
│       └── places.py              # /api/v1/places (조회/등록)
├── core/config.py
├── db/{session,init_db,base}.py
├── models/
│   ├── place.py
│   └── place_summary_embedding.py
├── schemas/
└── services/
    ├── llm.py                   # OpenAI 래퍼 (요약/카테고리 추출/임베딩)
    └── recommendation.py        # 추천 알고리즘 + 임베딩 갱신

scripts/
├── crawl_near_stations.py       # 역 기반 장소 수집 → places 테이블
├── naver_crawl_xy.py            # 위 스크립트가 내부 사용
├── fast_review_summarize.py     # 리뷰 크롤 + 요약 + 임베딩 (병렬)
├── create_indexes.py            # DB 인덱스 생성
└── test_recommend_quality.py    # 추천 품질 회귀 테스트
```

## 환경 변수 (`.env`)

```
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5434/ggud_ai
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_RESPONSE_MODEL=gpt-4o-mini
RECOMMENDATION_DEFAULT_RADIUS_KM=5.0
```

## 실행

### 1) DB 기동

```bash
docker compose up -d           # localhost:5434 에 pgvector 컨테이너
```

### 2) 의존성

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium    # 크롤링 스크립트용
```

### 3) FastAPI 서버

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- 헬스체크: `GET /health`

## 데이터 파이프라인

### 장소 수집

```bash
python scripts/crawl_near_stations.py
```

지하철역 좌표 주변에서 음식점/카페를 검색해 `places` 테이블에 적재한다.

### 리뷰 요약 + 임베딩

```bash
# review_count 상위부터 처리 (이미 임베딩 있는 곳은 자동 스킵)
python scripts/fast_review_summarize.py --workers 3 --min-reviews 30 --max-count 50

# 큐 앞부분 건너뛰기 (병렬 분산)
python scripts/fast_review_summarize.py --workers 3 --skip 8000

# 한 장소만 디버깅
python scripts/fast_review_summarize.py --debug-place-id 1053282197
```

주요 옵션:
- `--workers N` — Playwright 워커 수 (페이지/리뷰 병렬도)
- `--min-reviews N` — `review_count >= N` 인 장소만 처리
- `--max-count N` — 한 장소당 최대 리뷰 수
- `--skip N` — 큐 앞 N개 건너뛰기
- `--shards S --shard I` — 여러 인스턴스가 같은 큐를 나눠 처리
- `--no-skip-done` — 임베딩 있는 장소도 강제 재처리

## API

### `POST /recommend-places`  (Spring → AI)

요청
```json
{
  "promise_id": 1,
  "query": "조용한 파스타집 데이트",
  "latitude": 37.4979,
  "longitude": 127.0276,
  "limit": 10,
  "tab": "RESTAURANT",
  "user_id": 123,
  "past_place_ids": [101, 202]
}
```

응답
```json
{
  "promise_id": 1,
  "recommendations": [
    {
      "place_id": "37801085",
      "ai_score": 91.4,
      "similarity_score": 0.6234,
      "distance_from_midpoint": 0.8,
      "place_name": "파이브테이블즈",
      "category": "양식",
      "address": "...",
      "image_url": "...",
      "summary_text": "..."
    }
  ]
}
```

- `query` 가 있으면 카테고리 추출 + 가중합 점수 기반 추천
- `query` 가 비어 있고 `past_place_ids` 가 있으면 과거 선택 장소들의 평균 임베딩 기반 개인화 추천
- 둘 다 없으면 위치 기반 거리순

### 추천 점수 산식 (`recommend_places`)

```
similarity   = 1 - cosine_distance(query_embed, place_category_embed)
weighted_sum = Σ similarity_k × CATEGORY_WEIGHTS[k]
                # menu 0.4, mood/purpose/companion 0.2
+ place_type 매칭 보너스 (0.05) — soft 필터일 때
× 인기도 가중치 (0.85 + 0.15 × log10(review_count)/4.5)
```

`ai_score = min(100, score / 합계가중치 × 100)`

## 회귀 테스트

```bash
python scripts/test_recommend_quality.py
```

여러 쿼리(떡볶이/스터디카페/파스타/술집/와인바…)에 대해 LLM 추출과 최종 추천 결과를 한 번에 출력한다.

## Docker

`Dockerfile` 로 이미지화 가능. 로컬에서는 `docker compose up -d` 로 DB 만 띄우고 uvicorn 은 로컬 실행이 편하다.
