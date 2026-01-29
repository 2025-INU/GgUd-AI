# FastAPI AI Recommendation Server

Spring Boot 백엔드와 통합된 AI 추천 서버입니다.

## 구조

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

## 실행

```bash
# 개발 모드
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

# 리뷰 크롤링
python scripts/review_crawl.py --place-id 123456 --max-count 100
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

```bash
# 이미지 빌드
docker build -t ggud-ai-server .

# 컨테이너 실행
docker run -p 8000:8000 --env-file .env ggud-ai-server
```

또는 통합 Docker Compose 사용:
```bash
# 루트에서
docker-compose -f docker-compose.prod.yml up -d ai-server
```
