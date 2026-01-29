import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

def _load_dotenv_fallback() -> None:
    """python-dotenv 없이도 .env를 읽어서 os.environ에 주입."""
    # backend 폴더의 .env 파일 찾기
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        return


# .env 파일 로드 (python-dotenv 사용, 없으면 fallback)
try:
    from dotenv import load_dotenv  # type: ignore

    # backend 폴더의 .env 파일 찾기
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        _load_dotenv_fallback()
except Exception:
    _load_dotenv_fallback()

import aiohttp
from playwright.async_api import async_playwright

# backend 폴더 내부에서 실행되므로 상대 경로로 import
import sys
from pathlib import Path
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# S3 업로드 (선택적)
try:
    from utils.s3_storage import S3StorageManager
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 타임아웃 상수 (ms)
TIMEOUT = 10000


class ReviewStorageManager:
    """JSONL 기반 리뷰 저장소"""

    def __init__(self, output_path: str = "reviews.jsonl") -> None:
        self.path = Path(output_path)
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_existing_review_ids(self) -> Set[str]:
        """기존 리뷰 ID 집합 로드"""
        if not self.path.exists():
            return set()

        review_ids: Set[str] = set()
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                review_id = data.get("id") or data.get("review_id")
                if review_id:
                    review_ids.add(str(review_id))
        return review_ids

    def append(self, reviews: List[Dict]) -> None:
        """리뷰 데이터 추가 저장"""
        if not reviews:
            return
        with self.path.open("a", encoding="utf-8") as f:
            for review in reviews:
                f.write(json.dumps(review, ensure_ascii=False) + "\n")


class NaverMapReviewCrawler:
    def __init__(self, headless: bool = True, verbose: bool = True):
        self.headless = headless
        self.verbose = verbose
        self.launch_options = self._get_launch_options()

    def _get_launch_options(self) -> dict:
        """브라우저 실행 옵션 반환"""
        return {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-web-security",
                "--disable-site-isolation-trials",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
                "--hide-scrollbars",
                "--metrics-recording-only",
                "--mute-audio",
                "--safebrowsing-disable-auto-update",
                "--ignore-certificate-errors",
                "--ignore-ssl-errors",
                "--ignore-certificate-errors-spki-list",
                "--disable-setuid-sandbox",
                "--window-size=1920,1080",
                "--start-maximized",
            ],
        }

    def _get_context_options(self) -> dict:
        """브라우저 컨텍스트 옵션 반환"""
        return {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "locale": "ko-KR",
            "timezone_id": "Asia/Seoul",
            "permissions": ["geolocation"],
            "geolocation": {"latitude": 37.5665, "longitude": 126.9780},
            "color_scheme": "light",
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
            "extra_http_headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "max-age=0",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
                "Upgrade-Insecure-Requests": "1",
            },
        }

    def _generate_review_id(
        self, review_id: Optional[str] = None, author_name: str = "", review_text: str = "", visit_date: str = ""
    ) -> str:
        """리뷰 고유 ID 생성"""
        if review_id:
            return str(review_id)
        # Fallback: 해시 기반 ID 생성
        hash_input = f"{author_name}|{review_text}|{visit_date}"
        return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

    async def _extract_reviews_from_apollo(self, page, place_id: str) -> List[Dict]:
        """__APOLLO_STATE__에서 리뷰 데이터 추출"""
        apollo_data = await page.evaluate("""
            () => {
                if (window.__APOLLO_STATE__) {
                    const apollo = window.__APOLLO_STATE__;
                    const reviews = [];
                    
                    // VisitorReviews 키에서 리뷰 추출
                    for (const key in apollo) {
                        if (key.startsWith('VisitorReviews:')) {
                            const data = apollo[key];
                            if (data && (data.review || data.reviewId)) {
                                reviews.push({
                                    review_id: data.reviewId || key.split(':')[1],
                                    review: data.review || '',
                                    // HTML 태그 제거 (필요시)
                                    review_text: data.review ? data.review.replace(/<[^>]*>/g, '') : '',
                                });
                            }
                        }
                    }
                    
                    // VisitorImages에서 추가 정보 추출 (작성자, 이미지 등)
                    const imagesMap = {};
                    for (const key in apollo) {
                        if (key.startsWith('VisitorImages:')) {
                            const imgData = apollo[key];
                            if (imgData && imgData.reviewId) {
                                imagesMap[imgData.reviewId] = {
                                    nickname: imgData.nickname || '',
                                    image_url: imgData.imageUrl || null,
                                    profile_image_url: imgData.profileImageUrl || null,
                                };
                            }
                        }
                    }
                    
                    // 리뷰와 이미지 정보 병합
                    reviews.forEach(review => {
                        const imgInfo = imagesMap[review.review_id];
                        if (imgInfo) {
                            review.author = imgInfo.nickname;
                            review.image_url = imgInfo.image_url;
                            review.profile_image_url = imgInfo.profile_image_url;
                        }
                    });
                    
                    return reviews;
                }
                return null;
            }
        """)
        
        if apollo_data and len(apollo_data) > 0:
            # 리뷰 데이터 정규화
            normalized_reviews = []
            for data in apollo_data:
                review_id = self._generate_review_id(
                    review_id=data.get("review_id"),
                    author_name=data.get("author", ""),
                    review_text=data.get("review_text", ""),
                )
                
                normalized_reviews.append({
                    "id": review_id,
                    "review_id": data.get("review_id"),
                    "place_id": place_id,
                    "author": data.get("author", "익명"),
                    "content": data.get("review_text") or data.get("review", ""),
                    "review_html": data.get("review", ""),  # HTML 원본도 저장
                    "image_url": data.get("image_url"),
                    "profile_image_url": data.get("profile_image_url"),
                    "visit_date": None,  # __APOLLO_STATE__에는 방문일이 없을 수 있음
                })
            
            return normalized_reviews
        
        return []

    async def _extract_reviews_from_dom(self, page, place_id: str) -> List[Dict]:
        """DOM에서 리뷰 데이터 추출 (Fallback)"""
        reviews = []
        
        try:
            review_elements = await page.query_selector_all("ul#_review_list > li.EjjAW")
            
            for elem in review_elements:
                # 작성자
                author_elem = await elem.query_selector("span.pui__NMi-Dp")
                author_name = await author_elem.inner_text() if author_elem else "익명"
                
                # 리뷰 내용
                content_elem = await elem.query_selector("div.pui__vn15t2 > a")
                review_text = await content_elem.inner_text() if content_elem else ""
                
                # 방문날짜
                date_elem = await elem.query_selector("time")
                visit_date = await date_elem.inner_text() if date_elem else ""
                
                # 고유 ID 생성
                review_id = self._generate_review_id(
                    author_name=author_name,
                    review_text=review_text,
                    visit_date=visit_date,
                )
                
                reviews.append({
                    "id": review_id,
                    "place_id": place_id,
                    "author": author_name,
                    "content": review_text,
                    "visit_date": visit_date,
                })
        except Exception as e:
            if self.verbose:
                logger.warning(f"DOM에서 리뷰 추출 실패: {str(e)}", file=sys.stderr)
        
        return reviews

    async def _load_more_reviews(self, page):
        """더보기 버튼 클릭 또는 스크롤로 더 많은 리뷰 로드"""
        # 더보기 버튼 찾기 (여러 셀렉터 시도)
        selectors = [
            "div.NSTUp a.fvwqf",  # 일반 더보기 버튼
            "a.fvwqf",  # 더보기 버튼 (간단한 셀렉터)
            "button:has-text('더보기')",  # 텍스트로 찾기
            "a:has-text('더보기')",
        ]
        
        for selector in selectors:
            try:
                more_button = await page.query_selector(selector)
                if more_button:
                    is_visible = await more_button.is_visible()
                    if is_visible:
                        await more_button.scroll_into_view_if_needed()
                        await asyncio.sleep(0.5)
                        await more_button.click()
                        await asyncio.sleep(2)  # 새 리뷰 로딩 대기
                        if self.verbose:
                            logger.info(f"더보기 버튼 클릭: {selector}")
                        return True
            except Exception as e:
                if self.verbose:
                    logger.debug(f"더보기 버튼 찾기 실패 ({selector}): {str(e)}")
                continue
        
        # 더보기 버튼이 없으면 스크롤
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)
        return False

    async def _scroll_to_load_all(self, page, max_count: int = 100):
        """모든 리뷰가 로드될 때까지 더보기 버튼 클릭 및 스크롤"""
        previous_count = 0
        no_change_count = 0
        max_no_change = 5  # 더보기 버튼이 여러 번 있을 수 있으므로 증가
        max_iterations = 50  # 무한 루프 방지

        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            
            # 현재 리뷰 개수 확인
            try:
                current_reviews = await page.query_selector_all("ul#_review_list > li.EjjAW")
                current_count = len(current_reviews)
            except Exception:
                current_count = 0

            if self.verbose and iteration % 5 == 0:
                logger.info(f"현재 {current_count}개 리뷰 로드됨 (목표: {max_count}개)")

            # 목표 개수 도달
            if current_count >= max_count:
                if self.verbose:
                    logger.info(f"목표 개수 {max_count}개 도달 (현재: {current_count}개)")
                break

            if current_count == previous_count:
                no_change_count += 1
                if no_change_count >= max_no_change:
                    if self.verbose:
                        logger.info("더 이상 로드할 리뷰가 없습니다.")
                    break
            else:
                no_change_count = 0

            previous_count = current_count

            # 더보기 버튼 클릭 또는 스크롤
            await self._load_more_reviews(page)

    async def crawl_all_reviews(
        self, place_id: str, existing_ids: Set[str], max_count: int = 100
    ) -> List[Dict]:
        """네이버 지도의 리뷰를 크롤링 (최대 max_count개까지)
        
        Args:
            place_id: 네이버 place_id
            existing_ids: 이미 존재하는 리뷰 id의 set. 발견 시 즉시 중단 (필수).
            max_count: 수집할 리뷰의 최대 개수 (기본 100).
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(**self.launch_options)
            context = await browser.new_context(**self._get_context_options())
            page = await context.new_page()

            await page.route(
                "**/*.{png,jpg,jpeg,gif,svg,webp}", lambda route: route.abort()
            )

            reviews: List[Dict] = []
            already_appended_ids: Set[str] = set()

            try:
                # 리뷰 페이지로 이동
                url = f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor"
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)  # 페이지 로딩 대기

                # 최신순 정렬 시도
                try:
                    sort_buttons = await page.query_selector_all("a.place_btn_option")
                    for btn in sort_buttons:
                        btn_text = await btn.inner_text()
                        if "최신순" in btn_text:
                            await btn.click()
                            await asyncio.sleep(2)
                            if self.verbose:
                                logger.info("최신순으로 정렬됨")
                            break
                except Exception:
                    pass  # 정렬 실패해도 계속 진행

                # 먼저 __APOLLO_STATE__에서 리뷰 추출 시도
                apollo_reviews = await self._extract_reviews_from_apollo(page, place_id)
                
                if apollo_reviews and len(apollo_reviews) > 0:
                    if self.verbose:
                        logger.info(f"__APOLLO_STATE__에서 {len(apollo_reviews)}개 리뷰 추출")
                    
                    # 중복 체크 및 필터링
                    for review in apollo_reviews:
                        review_id = review.get("id")
                        if review_id in existing_ids:
                            if self.verbose:
                                logger.info(f"이미 존재하는 리뷰(id={review_id}) 발견, 크롤링 중단")
                            return reviews[:max_count] if len(reviews) > max_count else reviews
                        
                        if review_id not in already_appended_ids:
                            reviews.append(review)
                            already_appended_ids.add(review_id)
                            
                            if len(reviews) >= max_count:
                                break
                    
                    # __APOLLO_STATE__에서 충분한 리뷰를 가져왔으면 반환
                    if len(reviews) >= max_count:
                        return reviews[:max_count]
                    
                    # 부족하면 더보기 버튼 클릭하여 더 로드
                    if self.verbose:
                        logger.info(f"__APOLLO_STATE__에서 {len(reviews)}개만 추출됨, 더 로드 시도...")
                    
                    # 더보기 버튼 클릭하여 더 많은 리뷰 로드
                    await self._scroll_to_load_all(page, max_count=max_count)
                    
                    # 다시 __APOLLO_STATE__ 확인 (새로 로드된 리뷰)
                    apollo_reviews_2 = await self._extract_reviews_from_apollo(page, place_id)
                    if apollo_reviews_2:
                        for review in apollo_reviews_2:
                            review_id = review.get("id")
                            if review_id in existing_ids:
                                if self.verbose:
                                    logger.info(f"이미 존재하는 리뷰(id={review_id}) 발견")
                                break
                            
                            if review_id not in already_appended_ids:
                                reviews.append(review)
                                already_appended_ids.add(review_id)
                                
                                if len(reviews) >= max_count:
                                    break
                    
                    if len(reviews) >= max_count:
                        return reviews[:max_count]
                
                # __APOLLO_STATE__가 없거나 부족하면 DOM 방식 사용
                if self.verbose:
                    logger.info("__APOLLO_STATE__ 없음 또는 부족, DOM 방식으로 추출")
                
                # 더보기 버튼 클릭 및 스크롤하여 모든 리뷰 로드
                await self._scroll_to_load_all(page, max_count=max_count)
                
                # DOM에서 리뷰 추출 (반복적으로 추출하여 최신 상태 유지)
                while len(reviews) < max_count:
                    dom_reviews = await self._extract_reviews_from_dom(page, place_id)
                    
                    new_reviews_added = False
                    for review in dom_reviews:
                        review_id = review.get("id")
                        if review_id in existing_ids:
                            if self.verbose:
                                logger.info(f"이미 존재하는 리뷰(id={review_id}) 발견, 크롤링 중단")
                            return reviews[:max_count] if len(reviews) > max_count else reviews
                        
                        if review_id not in already_appended_ids:
                            reviews.append(review)
                            already_appended_ids.add(review_id)
                            new_reviews_added = True
                            
                            if len(reviews) >= max_count:
                                break
                    
                    # 새로운 리뷰가 없으면 종료
                    if not new_reviews_added:
                        if self.verbose:
                            logger.info("더 이상 새로운 리뷰가 없습니다.")
                        break
                    
                    # 목표 개수 도달
                    if len(reviews) >= max_count:
                        break
                    
                    # 더보기 버튼 다시 클릭 시도
                    await self._load_more_reviews(page)
                    await asyncio.sleep(1)

            except Exception as e:
                if self.verbose:
                    logger.error(f"크롤링 중 오류: {str(e)}", file=sys.stderr)
            finally:
                await browser.close()

            return reviews[:max_count] if len(reviews) > max_count else reviews


async def crawl_reviews(
    place_id: str, max_count: int = 100, headless: bool = True, verbose: bool = True
) -> List[Dict]:
    """Convenience wrapper to get reviews."""
    crawler = NaverMapReviewCrawler(headless=headless, verbose=verbose)
    return await crawler.crawl_all_reviews(place_id, set(), max_count=max_count)


def print_results_summary(reviews: List[Dict]):
    """결과 요약 출력"""
    print(f"\n총 {len(reviews)}개의 리뷰를 수집했습니다.")
    for i, review in enumerate(reviews[:10], 1):
        author = review.get("author", "익명")
        content = review.get("content", "")[:50]
        visit_date = review.get("visit_date", "")
        print(f"\n{i}. 작성자: {author}")
        print(f"   내용: {content}...")
        if visit_date:
            print(f"   방문일: {visit_date}")


def run_cli() -> None:
    parser = argparse.ArgumentParser(description="네이버 지도 리뷰 크롤러")
    parser.add_argument("--place-id", required=True, help="네이버 place_id")
    parser.add_argument("--max-count", type=int, default=100, help="최대 리뷰 수집 개수")
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="stdout에 JSON 배열 출력 (API 연동용)",
    )
    parser.add_argument(
        "--jsonl-path",
        type=Path,
        default=None,
        help="저장할 JSONL 경로 (기본: reviews.jsonl)",
    )
    parser.add_argument(
        "--skip-jsonl",
        action="store_true",
        help="JSONL 저장을 건너뜀 (API 호출용)",
    )
    parser.add_argument(
        "--s3-bucket",
        type=str,
        default=os.getenv("S3_BUCKET_NAME") or os.getenv("S3_BUCKET"),
        help="S3 버킷 이름 (.env의 S3_BUCKET_NAME 또는 S3_BUCKET 사용 가능)",
    )
    parser.add_argument(
        "--aws-access-key-id",
        type=str,
        default=None,
        help="AWS Access Key ID (환경변수 AWS_ACCESS_KEY_ID 사용 가능)",
    )
    parser.add_argument(
        "--aws-secret-access-key",
        type=str,
        default=None,
        help="AWS Secret Access Key (환경변수 AWS_SECRET_ACCESS_KEY 사용 가능)",
    )
    parser.add_argument(
        "--download-images",
        action="store_true",
        help="리뷰 이미지를 다운로드하여 S3에 업로드 (--s3-bucket 필요)",
    )
    args = parser.parse_args()

    reviews = asyncio.run(crawl_reviews(args.place_id, args.max_count, verbose=not args.json_output))

    # storage_manager append (optional)
    new_results = reviews
    if not args.skip_jsonl:
        if args.jsonl_path:
            manager = ReviewStorageManager(str(args.jsonl_path))
        else:
            manager = ReviewStorageManager()
        existing = manager.load_existing_review_ids()
        new_results = [
            item
            for item in reviews
            if item.get("id") and item["id"] not in existing
        ]
        manager.append(new_results)

    # S3 업로드 및 이미지 다운로드 (선택적)
    # .env에서 버킷 이름이 있으면 자동으로 사용
    s3_bucket = args.s3_bucket or os.getenv("S3_BUCKET_NAME") or os.getenv("S3_BUCKET")
    
    if s3_bucket and S3_AVAILABLE:
        try:
            s3_manager = S3StorageManager(
                bucket_name=s3_bucket,
                aws_access_key_id=args.aws_access_key_id or os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=args.aws_secret_access_key or os.getenv("AWS_SECRET_ACCESS_KEY"),
            )
            
            if not args.json_output:
                print(f"\nS3 업로드 시작 (버킷: {s3_bucket})")
            
            # 리뷰 원본 데이터 업로드
            # NOTE: JSONL은 "신규만 append"라서 new_results가 0개일 수 있음(중복이면 전부 스킵).
            # S3에는 원본 스냅샷(이번 실행에서 크롤링한 전체 reviews)을 올리는 게 맞음.
            uploaded_count = 0
            try:
                if reviews:
                    s3_manager.upload_reviews(
                        place_id=str(args.place_id),
                        reviews=reviews,
                    )
                uploaded_count = len(reviews)
                if not args.json_output:
                    print(f"\nS3에 {uploaded_count}개 리뷰 원본 데이터 업로드 완료")
            except Exception as e:
                if not args.json_output:
                    print(f"S3 리뷰 업로드 실패: {str(e)}", file=sys.stderr)
            
            # 이미지 다운로드 및 업로드
            if args.download_images:
                image_count = 0
                async def download_and_upload_image(review_id: str, image_url: str):
                    nonlocal image_count
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                                if response.status == 200:
                                    image_data = await response.read()
                                    image_name = f"{int(time.time())}_{image_count}.jpg"
                                    s3_manager.upload_review_image(
                                        review_id=review_id,
                                        image_name=image_name,
                                        image_data=image_data
                                    )
                                    image_count += 1
                                    return True
                    except Exception as e:
                        if not args.json_output:
                            logger.debug(f"이미지 다운로드 실패 (review_id={review_id}): {str(e)}")
                        return False
                    return False
                
                # 비동기로 이미지 다운로드
                async def download_all_images():
                    tasks = []
                    for review in new_results:
                        review_id = review.get("id") or review.get("review_id", "")
                        image_url = review.get("image_url")
                        if image_url and review_id:
                            tasks.append(download_and_upload_image(review_id, image_url))
                    
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                        if not args.json_output:
                            print(f"S3에 {image_count}개 리뷰 이미지 업로드 완료")
                
                asyncio.run(download_all_images())
                
        except Exception as e:
            if not args.json_output:
                print(f"S3 업로드 초기화 실패: {str(e)}", file=sys.stderr)
    elif s3_bucket and not S3_AVAILABLE:
        if not args.json_output:
            print("S3 업로드를 위해 s3_storage 모듈이 필요합니다.", file=sys.stderr)
    elif args.download_images and not s3_bucket:
        if not args.json_output:
            print("이미지 다운로드를 위해 --s3-bucket 옵션이 필요합니다.", file=sys.stderr)

    if args.json_output:
        print(json.dumps(new_results, ensure_ascii=False))
    else:
        print_results_summary(new_results)
        print(f"\n새로 저장한 리뷰 수: {len(new_results)}개 (중복 제외)")


if __name__ == "__main__":
    run_cli()
