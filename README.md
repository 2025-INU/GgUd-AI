# FastAPI AI Recommendation Server

Spring Boot 백엔드와 통합된 AI 추천 서버입니다.

## 구조

추천 시스템 설계·구현 방식은 **[docs/RECOMMENDATION.md](docs/RECOMMENDATION.md)** 에 정리되어 있습니다.

```
backend/
├── app/                    # FastAPI 애플리케이션
│   ├── api/               # API 엔드포인트
│   ├── services/          # 비즈니스 로직
│   ├── models/            # DB 모델
│   └── main.py            # 애플리케이션 진입점
│
├── scripts/                # 크롤링 및 데이터 관리 스크립트
│   ├── naver_crawl.py     # 네이버 지도 장소 크롤링
│   ├── review_crawl.py    # 리뷰 크롤링
│   ├── load_places.py     # DB 적재
│   └── generate_embeddings.py  # 임베딩 생성
│
├── utils/                  # 유틸리티 모듈
│   ├── storage_manager.py # JSONL 저장소 관리
│   └── s3_storage.py      # S3 저장소 관리
│
├── Dockerfile             # Docker 이미지 빌드
├── requirements.txt       # Python 의존성
└── README.md             # 이 파일
```

## 설치

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 환경 변수 설정

`backend/.env` 파일 생성:

```bash
# Database
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/ggud_db
DB_HOST=localhost
DB_PORT=5432
DB_USERNAME=postgres
DB_PASSWORD=postgres
DB_NAME=ggud_db

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_RESPONSE_MODEL=gpt-4o-mini

# Application
PROJECT_NAME=Meetup Recommender API
API_V1_PREFIX=/api/v1
RECOMMENDATION_TOP_K=5

# AWS S3 (선택)
AWS_ACCESS_KEY_ID=your-aws-access-key-id
AWS_SECRET_ACCESS_KEY=your-aws-secret-access-key
AWS_REGION=ap-northeast-2
S3_BUCKET_NAME=ggud-places-data
```

### 데이터베이스 분리 (GgUd-AI 전용 DB)

- **GgUd-AI**는 장소·리뷰·리뷰 임베딩만 사용합니다. `DATABASE_URL`은 이 데이터를 담는 **전용 DB**로 두는 것을 권장합니다.
- **Spring Backend**는 별도 DB를 사용하며, Kakao Map·S3(메타데이터) 조회는 Backend에서 수행합니다.
- 배포 시: ECS 크롤링 → S3 업로드 → SQS → Lambda → RDS(places/reviews/review_embeddings) 파이프라인으로 동일하게 구성할 수 있습니다.

## 실행

### 방법 1: 전체 Docker로 실행 (DB + API 한 번에)

**1) Docker 네트워크 생성 (최초 1회)**  
```bash
docker network create ggud-network
```

**2) API 이미지 빌드**  
```bash
cd /Users/ung/캡스톤/ggud_local/GgUd-AI
docker build -t ggud/ggud-ai:latest .
```

**3) 서비스 기동**  
```bash
docker compose up -d
```

- DB는 `localhost:5433`, API는 `http://localhost:8000` 에 떠 있습니다.
- 로그 보기: `docker compose logs -f ai-server`
- 중지: `docker compose down`

코드 수정 후 반영하려면 다시 빌드 후 재기동해야 합니다.  
```bash
docker compose build --no-cache ai-server && docker compose up -d ai-server
```

---

### 방법 2: 로컬에서 API만 실행 (DB만 Docker)

코드 수정 시 바로 반영되게 하려면 API는 로컬 uvicorn, DB만 Docker로 띄우는 방식이 편합니다.

**1) DB만 Docker로 기동**  
```bash
cd /Users/ung/캡스톤/ggud_local/GgUd-AI
docker network create ggud-network   # 최초 1회
docker compose up -d db-ai
```

**2) .env 확인**  
- `DATABASE_URL=postgresql+psycopg2://ggud_user:ggud_db_pw@localhost:5433/ggud_db` 처럼 **localhost:5433** 이어야 합니다.

**3) 가상환경 활성화 후 API 실행**  
```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API: http://localhost:8000  
- 파일 저장 시 자동 리로드됩니다.

---

### 요약

| 구분 | 방법 1 (전체 Docker) | 방법 2 (로컬 API) |
|------|----------------------|--------------------|
| DB | Docker `db-ai` | Docker `db-ai` |
| API | Docker `ai-server` | 로컬 uvicorn |
| 코드 반영 | 이미지 재빌드 필요 | 저장 시 자동 리로드 |
| 사용 예 | 배포·동작 확인용 | 일상 개발용 |

```bash
# 개발 모드 (방법 2 사용 시)
uvicorn app.main:app --reload

# 프로덕션 모드
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API 엔드포인트

### Spring Boot 통합
- `POST /recommend-places` - 장소 추천 (Spring Boot 호출용)

### 일반 API
- `GET /api/v1/places` - 장소 목록 조회
- `POST /api/v1/crawl` - 장소 크롤링
- `POST /api/v1/crawl/reviews` - 리뷰 크롤링 및 임베딩 생성

## 스크립트 사용법

### 크롤링
```bash
# 장소 크롤링
python scripts/naver_crawl.py --query "홍대 카페" --limit 50

# 리뷰 크롤링 (DB 적재 + 선택적 S3 업로드)
python scripts/crawl_reviews_from_db.py --max-count 100
# .env에 S3_BUCKET_NAME이 있으면 리뷰를 S3에 reviews/{place_id}/reviews.json 으로 업로드
python scripts/crawl_reviews_from_db.py --place-ids 123,456 --max-count 50
```

### DB 관리
```bash
# DB 스키마 초기화
python scripts/init_db_schema.py

# 장소 데이터 적재
python scripts/load_places.py

# 임베딩 생성
python scripts/generate_embeddings.py
```

## Docker 배포

전체 실행 방법은 위 **방법 1: 전체 Docker로 실행** 을 참고하세요.

```bash
# 이미지만 단독 실행할 때
docker build -t ggud-ai-server .
docker run -p 8000:8000 --env-file .env ggud-ai-server
```
