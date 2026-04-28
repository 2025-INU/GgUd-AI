import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

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

# DB 적재 (선택적, DATABASE_URL 있을 때)
try:
    from app.db.session import SessionLocal
    from app.services.recommendation import upsert_place
    from datetime import datetime
    DB_AVAILABLE = True
except Exception:
    SessionLocal = None
    upsert_place = None
    datetime = None
    DB_AVAILABLE = False

# 타임아웃 상수 (ms)
TIMEOUT = 20000


class NaverMapPlaceCrawler:
    def __init__(self, headless: bool = True, verbose: bool = True, enrich_images: bool = True):
        self.headless = headless
        self.verbose = verbose
        self.enrich_images = enrich_images
        self.launch_options = self._get_launch_options()
        # 상세 페이지 보강(주소/이미지) 동시성 제한 (너무 크게 하면 차단/느려짐)
        self.detail_concurrency = 5

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

    async def _perform_search(self, page, search_query: str):
        """검색 수행"""
        await page.goto("https://httpbin.org/ip")
        await page.goto("https://map.naver.com/", wait_until="domcontentloaded")

        search_input = await page.wait_for_selector(
            "input.input_search", state="visible", timeout=TIMEOUT
        )
        await search_input.click()
        await search_input.fill(search_query)
        await search_input.press("Enter")

        await page.wait_for_selector(
            "iframe#searchIframe", state="visible", timeout=TIMEOUT
        )

    async def _get_search_frame(self, page):
        """검색 결과 iframe 가져오기"""
        iframe_element = await page.query_selector("iframe#searchIframe")
        return await iframe_element.content_frame()

    async def _extract_thumbnail_map(self, frame) -> dict[str, str]:
        """검색 결과 리스트에서 place_id -> thumbnail(src) 맵을 추출."""
        if not frame:
            return {}
        try:
            rows = await frame.evaluate(
                """
                () => {
                    const out = [];
                    const items = document.querySelectorAll('li.UEzoS');
                    for (const li of items) {
                        const a = li.querySelector('a.place_bluelink');
                        if (!a) continue;
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/\\/place\\/(\\d+)/);
                        if (!m) continue;
                        const placeId = m[1];
                        // 이미지 셀렉터는 종종 바뀜: img 태그를 최대한 넓게 탐색
                        const img = li.querySelector('img');
                        const src = img?.getAttribute('src') || img?.getAttribute('data-src') || '';
                        if (src && src.startsWith('http')) {
                            out.push([placeId, src]);
                        }
                    }
                    return out;
                }
                """
            )
            return {pid: src for pid, src in rows} if rows else {}
        except Exception:
            return {}

    async def _scroll_to_load_all(self, frame):
        """모든 결과가 로드될 때까지 스크롤"""
        previous_count = 0
        no_change_count = 0
        max_no_change = 3

        while True:
            current_places = await frame.query_selector_all("li.UEzoS")
            current_count = len(current_places)

            if current_count == previous_count:
                no_change_count += 1
                if no_change_count >= max_no_change:
                    if self.verbose:
                        print("더 이상 로드할 데이터가 없습니다.", file=sys.stderr)
                    break
            else:
                no_change_count = 0

            previous_count = current_count

            await frame.evaluate(
                """
                () => {
                    const scrollContainer = document.querySelector('.Ryr1F') || 
                                           document.querySelector('[role="main"]') || 
                                           document.body;
                    
                    if (scrollContainer) {
                        scrollContainer.scrollTop = scrollContainer.scrollHeight;
                    } else {
                        window.scrollTo(0, document.body.scrollHeight);
                    }
                }
            """
            )

            await asyncio.sleep(2)

    async def _extract_basic_info(self, place):
        """장소 기본 정보 추출"""
        name_elem = await place.query_selector("span.TYaxT")
        name = await name_elem.inner_text() if name_elem else "이름 없음"

        category_elem = await place.query_selector("span.KCMnt")
        category = await category_elem.inner_text() if category_elem else ""

        return name, category

    async def _extract_place_id(self, place, page):
        """place_id 추출"""
        link_elem = await place.query_selector("a.place_bluelink")
        if not link_elem:
            return None

        await link_elem.click()
        await page.wait_for_url(lambda url: "/place/" in url, timeout=TIMEOUT)

        new_url = page.url
        match = re.search(r"/place/(\d+)", new_url)
        return match.group(1) if match else None

    async def _extract_address_info(self, place_id: str, context):
        """주소 및 좌표 정보 추출"""
        place_detail_url = f"https://pcmap.place.naver.com/place/{place_id}"
        detail_page = await context.new_page()

        origin_address: Optional[str] = None
        road_address: Optional[str] = None
        latitude: Optional[float] = None
        longitude: Optional[float] = None
        image_url: Optional[str] = None

        try:
            await detail_page.goto(place_detail_url, wait_until="domcontentloaded")
            await detail_page.wait_for_timeout(2000)  # 페이지 로딩 대기

            # 먼저 __NEXT_DATA__에서 데이터 추출 시도 (가장 안정적)
            data = await detail_page.evaluate(
                """
                () => {
                    if (window.__NEXT_DATA__ && window.__NEXT_DATA__.props) {
                        const props = window.__NEXT_DATA__.props;
                        if (props.pageProps && props.pageProps.initialState) {
                            const state = props.pageProps.initialState;
                            if (state.place && state.place.location) {
                                // 대표 이미지 URL은 버전별로 필드명이 자주 바뀌므로 가능한 후보를 넓게 탐색한다.
                                const place = state.place;
                                const candidates = [];
                                // 흔한 케이스들
                                if (place.imageUrl) candidates.push(place.imageUrl);
                                if (place.thumbnailUrl) candidates.push(place.thumbnailUrl);
                                if (place.thumUrl) candidates.push(place.thumUrl);
                                if (place.mainPhotoUrl) candidates.push(place.mainPhotoUrl);
                                // photos 배열 형태
                                if (Array.isArray(place.photos) && place.photos.length > 0) {
                                    const p0 = place.photos[0];
                                    if (p0?.url) candidates.push(p0.url);
                                    if (p0?.imageUrl) candidates.push(p0.imageUrl);
                                    if (p0?.originUrl) candidates.push(p0.originUrl);
                                }
                                // images 배열 형태
                                if (Array.isArray(place.images) && place.images.length > 0) {
                                    const i0 = place.images[0];
                                    if (typeof i0 === "string") candidates.push(i0);
                                    if (i0?.url) candidates.push(i0.url);
                                    if (i0?.imageUrl) candidates.push(i0.imageUrl);
                                }
                                const imageUrl = candidates.find((v) => typeof v === "string" && v.startsWith("http")) || null;
                                return {
                                    roadAddress: state.place.location.roadAddress,
                                    address: state.place.location.address,
                                    x: state.place.location.x || state.place.location.lng,
                                    y: state.place.location.y || state.place.location.lat,
                                    imageUrl,
                                };
                            }
                        }
                    }
                    return null;
                }
            """
            )

            if data:
                road_address = data.get("roadAddress") or data.get("address")
                origin_address = data.get("address") or data.get("roadAddress")
                try:
                    longitude = float(data.get("x")) if data.get("x") else None
                    latitude = float(data.get("y")) if data.get("y") else None
                except (TypeError, ValueError):
                    pass
                image_url = data.get("imageUrl") or None

            # __NEXT_DATA__에서 못 찾았으면 DOM에서 시도 (fallback)
            if not origin_address or not road_address:
                try:
                    # 타임아웃을 짧게 설정 (5초)
                    await detail_page.wait_for_selector("span.LDgIH", timeout=5000, state="visible")
                    address_elems = await detail_page.query_selector_all("span.LDgIH")
                    if address_elems:
                        text_values = []
                        for elem in address_elems:
                            text = await elem.inner_text()
                            if text:
                                text_values.append(text)
                        if text_values:
                            origin_address = origin_address or text_values[0]
                            if len(text_values) > 1:
                                road_address = road_address or text_values[1]
                except Exception:
                    # 여러 셀렉터 시도
                    selectors = ["span.LDgIH", ".LDgIH", "[class*='address']", "[class*='LDgIH']"]
                    for selector in selectors:
                        try:
                            elem = await detail_page.query_selector(selector)
                            if elem:
                                text = await elem.inner_text()
                                if text:
                                    origin_address = origin_address or text
                                    break
                        except:
                            continue
        except Exception as e:
            if self.verbose:
                print(f"주소 추출 실패 (place_id={place_id}): {str(e)}", file=sys.stderr)

            # 좌표가 없으면 스크립트에서 찾기
            if latitude is None or longitude is None:
                coord_data = await detail_page.evaluate(
                    """
                    () => {
                        const scripts = document.querySelectorAll('script');
                        for (let script of scripts) {
                            const text = script.textContent || script.innerText;
                            if (!text) continue;
                            if (text.includes('"x"') && text.includes('"y"')) {
                                try {
                                    const x = text.match(/"x"\\s*:\\s*"([^"]+)"/);
                                    const y = text.match(/"y"\\s*:\\s*"([^"]+)"/);
                                    if (x && y) {
                                        return { x: x[1], y: y[1] };
                                    }
                                } catch (e) {}
                            }
                        }
                        return null;
                    }
                """
                )
                if coord_data:
                    try:
                        longitude = longitude or (
                            float(coord_data.get("x")) if coord_data.get("x") else None
                        )
                        latitude = latitude or (
                            float(coord_data.get("y")) if coord_data.get("y") else None
                        )
                    except (TypeError, ValueError):
                        pass
        finally:
            await detail_page.close()

        return {
            # DB에는 도로명 주소만 road_address로 저장
            "road_address": road_address or origin_address,
            "address": road_address or origin_address,
            "latitude": latitude,
            "longitude": longitude,
            "image_url": image_url,
        }

    async def _enrich_image_urls_with_details(self, apollo_items: list[dict], context) -> list[dict]:
        """image_url이 없는 항목만 상세페이지로 보강(동시성 제한)."""
        if not apollo_items:
            return apollo_items

        sem = asyncio.Semaphore(self.detail_concurrency)

        async def enrich_one(item: dict) -> dict:
            place_id = item.get("place_id")
            if not place_id or item.get("image_url"):
                return item
            async with sem:
                try:
                    detail = await self._extract_address_info(str(place_id), context)
                    item["image_url"] = detail.get("image_url") or item.get("image_url")
                except Exception:
                    pass
            return item

        # 상세페이지 보강은 비용이 크므로 필요한 항목만 병렬 실행
        tasks = [enrich_one(d) for d in apollo_items if d.get("place_id") and not d.get("image_url")]
        if not tasks:
            return apollo_items
        await asyncio.gather(*tasks)
        return apollo_items

    async def _extract_place_data(self, places, frame, page, context, page_num: int = 1):
        """장소 데이터 추출 (__APOLLO_STATE__ 우선 사용)"""
        results = []

        # 먼저 iframe 내부의 __APOLLO_STATE__에서 데이터 추출 시도
        apollo_data = None
        if frame:
            try:
                apollo_data = await frame.evaluate("""
                    () => {
                        if (window.__APOLLO_STATE__) {
                            const apollo = window.__APOLLO_STATE__;
                            const places = [];
                            for (const key in apollo) {
                                if (key.startsWith('RestaurantListSummary:') || key.startsWith('Place:')) {
                                    const data = apollo[key];
                                    if (data && data.name && data.id) {
                                        // 방문자 리뷰 수(visitorReviewCount 또는 visitorReviewsTotal)를 우선 사용하고,
                                        // 없으면 totalReviewCount(방문자+블로그 등 합계)를 사용
                                        let reviewCount = 0;
                                        const visitor = data.visitorReviewCount || data.visitorReviewsTotal;
                                        const total = data.totalReviewCount;
                                        if (visitor) {
                                            const countStr = String(visitor).replace(/,/g, '');
                                            reviewCount = parseInt(countStr) || 0;
                                        } else if (total) {
                                            const countStr = String(total).replace(/,/g, '');
                                            reviewCount = parseInt(countStr) || 0;
                                        }
                                        
                                        places.push({
                                            place_id: data.id,
                                            name: data.name,
                                            category: data.category || data.businessCategory || '',
                                            address: data.roadAddress || data.address || null,
                                            latitude: data.y ? parseFloat(data.y) : null,
                                            longitude: data.x ? parseFloat(data.x) : null,
                                            review_count: reviewCount,
                                            image_url: data.imageUrl || data.thumbnailUrl || data.thumUrl || data.mainPhotoUrl || null,
                                        });
                                    }
                                }
                            }
                            return places;
                        }
                        return null;
                    }
                """)
            except Exception as e:
                if self.verbose:
                    print(f"iframe에서 __APOLLO_STATE__ 추출 실패: {str(e)}", file=sys.stderr)

        # iframe에서 못 찾았으면 메인 페이지에서 시도
        if not apollo_data:
            apollo_data = await page.evaluate("""
            () => {
                if (window.__APOLLO_STATE__) {
                    const apollo = window.__APOLLO_STATE__;
                    const places = [];
                    for (const key in apollo) {
                        if (key.startsWith('RestaurantListSummary:') || key.startsWith('Place:')) {
                            const data = apollo[key];
                            if (data && data.name && data.id) {
                                // 방문자 리뷰 수(visitorReviewCount 또는 visitorReviewsTotal)를 우선 사용하고,
                                // 없으면 totalReviewCount(방문자+블로그 등 합계)를 사용
                                let reviewCount = 0;
                                const visitor = data.visitorReviewCount || data.visitorReviewsTotal;
                                const total = data.totalReviewCount;
                                if (visitor) {
                                    const countStr = String(visitor).replace(/,/g, '');
                                    reviewCount = parseInt(countStr) || 0;
                                } else if (total) {
                                    const countStr = String(total).replace(/,/g, '');
                                    reviewCount = parseInt(countStr) || 0;
                                }
                                
                                places.push({
                                    place_id: data.id,
                                    name: data.name,
                                    category: data.category || data.businessCategory || '',
                                    address: data.roadAddress || data.address || null,
                                    latitude: data.y ? parseFloat(data.y) : null,
                                    longitude: data.x ? parseFloat(data.x) : null,
                                    review_count: reviewCount,
                                    image_url: data.imageUrl || data.thumbnailUrl || data.thumUrl || data.mainPhotoUrl || null,
                                });
                            }
                        }
                    }
                    return places;
                }
                return null;
            }
        """)

        if apollo_data and len(apollo_data) > 0:
            # __APOLLO_STATE__에서 데이터를 가져왔으면 바로 사용
            if self.verbose:
                print(f"__APOLLO_STATE__에서 {len(apollo_data)}개 장소 데이터 추출", file=sys.stderr)

            # 1) 먼저 리스트 DOM에서 thumbnail을 빠르게 확보
            thumb_map = await self._extract_thumbnail_map(frame)

            normalized = []
            for data in apollo_data:
                place_id = data.get("place_id")
                image_url = data.get("image_url") or (thumb_map.get(str(place_id)) if place_id else None)
                normalized.append({
                    "place_id": data.get("place_id"),
                    "name": data.get("name"),
                    "category": data.get("category"),
                    "page": page_num,
                    "road_address": data.get("address"),
                    "address": data.get("address"),
                    "latitude": data.get("latitude"),
                    "longitude": data.get("longitude"),
                    "review_count": data.get("review_count"),
                    "image_url": image_url,
                })

            # 2) 그래도 없는 항목만 상세페이지로 보강 (동시성 제한 병렬)
            if self.enrich_images:
                await self._enrich_image_urls_with_details(normalized, context)
            return normalized

        # __APOLLO_STATE__가 없으면 기존 방식 사용 (fallback)
        if self.verbose:
            print("__APOLLO_STATE__ 없음, 기존 방식으로 추출", file=sys.stderr)

        for place in places:
            name, category = await self._extract_basic_info(place)
            place_id = await self._extract_place_id(place, page)

            address_info = {}
            if place_id:
                address_info = await self._extract_address_info(place_id, context)

            results.append(
                {
                    "place_id": place_id,
                    "name": name,
                    "category": category,
                    "page": page_num,
                    "origin_address": address_info.get("origin_address"),
                    "address": address_info.get("address"),
                    "latitude": address_info.get("latitude"),
                    "longitude": address_info.get("longitude"),
                    "image_url": address_info.get("image_url"),
                }
            )

            await page.go_back()

        return results

    async def crawl_single_page(self, search_query: str) -> List[Dict]:
        """특정 페이지 하나만 크롤링"""
        async with async_playwright() as p:
            browser = await p.chromium.launch(**self.launch_options)
            context = await browser.new_context(**self._get_context_options())
            page = await context.new_page()

            await page.route(
                "**/*.{png,jpg,jpeg,gif,svg,webp}", lambda route: route.abort()
            )

            results = []

            try:
                # 검색 수행
                await self._perform_search(page, search_query)

                # iframe 가져오기
                frame = await self._get_search_frame(page)
                if not frame:
                    return results

                # 모든 결과 로드
                await self._scroll_to_load_all(frame)
                await frame.wait_for_selector(
                    "li.UEzoS", state="visible", timeout=TIMEOUT
                )

                # 데이터 추출
                places = await frame.query_selector_all("li.UEzoS")
                results = await self._extract_place_data(
                    places, frame, page, context, page_num=1
                )

                if self.verbose:
                    print(f"{len(places)}개 수집", file=sys.stderr)

            except Exception as e:
                if self.verbose:
                    print(f"크롤링 중 오류: {str(e)}", file=sys.stderr)
                else:
                    print(f"크롤링 중 오류: {str(e)}", file=sys.stderr)
            finally:
                await browser.close()

            return results


def merge_and_dedupe_results(
    all_results: List[List[Dict]], existing_place_ids: set
) -> List[Dict]:
    """결과 병합 및 중복 제거"""
    merged_results = []
    for page_results in all_results:
        merged_results.extend(page_results)

    return [
        item for item in merged_results if item["place_id"] not in existing_place_ids
    ]


def print_results_summary(results: List[Dict]):
    """결과 요약 출력"""
    print(f"\n총 {len(results)}개 신규 장소 수집")
    for i, place in enumerate(results, 1):
        place_id = place.get("place_id")
        name = place.get("name")
        category = place.get("category")
        page = place.get("page")
        origin_address = place.get("origin_address")
        road_address = place.get("address")
        common_address = place.get("common_address")
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        phone = place.get("phone")
        review_count = place.get("review_count")

        parts = [
            f"{i}. {place_id} [{name}]",
            f"[{category}]" if category else None,
            f"[page: {page}]" if page else None,
            f"[origin_address: {origin_address}]" if origin_address else None,
            f"[address: {road_address}]" if road_address else None,
            f"[common_address: {common_address}]" if common_address else None,
            f"[phone: {phone}]" if phone else None,
            f"[reviews: {review_count}]" if review_count else None,
            (
                f"[latitude: {latitude}, longitude: {longitude}]"
                if latitude is not None and longitude is not None
                else None
            ),
        ]
        print(" ".join(part for part in parts if part))


async def crawl_places(query: str, limit: Optional[int] = None, verbose: bool = True) -> List[Dict]:
    """Helper for CLI/API usage."""
    crawler = NaverMapPlaceCrawler(headless=True, verbose=verbose)
    results = await crawler.crawl_single_page(query)
    if limit is not None:
        return results[:limit]
    return results


def run_cli() -> None:
    parser = argparse.ArgumentParser(description="네이버 지도 장소 크롤러")
    parser.add_argument("--query", required=True, help="검색어 (예: 송도 맛집)")
    parser.add_argument("--limit", type=int, default=None, help="결과 최대 개수")
    parser.add_argument(
        "--thumbnail-only",
        action="store_true",
        help="대표 이미지 고화질 보강(상세 페이지) 없이 리스트 썸네일만 사용 (가장 빠름)",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="stdout에 JSON 배열 출력 (API 연동용)",
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
    args = parser.parse_args()

    crawler = NaverMapPlaceCrawler(
        headless=True,
        verbose=not args.json_output,
        enrich_images=not args.thumbnail_only,
    )
    results = asyncio.run(crawler.crawl_single_page(args.query))
    if args.limit is not None:
        results = results[: args.limit]

    # JSON 출력 모드(crawl_runner 연동)에서는 사이드 이펙트 없이 결과만 반환.
    # DB/S3 저장은 호출자(crawl_runner) 쪽에서 단일 경로로 처리한다.
    if (not args.json_output) and DB_AVAILABLE and os.getenv("DATABASE_URL") and results:
        db = SessionLocal()
        try:
            db_success = 0
            db_skip = 0
            for place in results:
                place_id_raw = place.get("place_id")
                if not place_id_raw:
                    db_skip += 1
                    continue
                try:
                    place_id = int(place_id_raw)
                except (ValueError, TypeError):
                    db_skip += 1
                    continue
                name = place.get("name")
                category = place.get("category") or "기타"
                origin_address = place.get("origin_address") or place.get("address") or ""
                latitude = place.get("latitude")
                longitude = place.get("longitude")
                if not name or latitude is None or longitude is None:
                    db_skip += 1
                    continue
                payload = {
                    "id": place_id,
                    "name": name,
                    "category": category,
                    # DB 컬럼명은 road_address만 허용됨
                    "road_address": origin_address,
                    "image_url": (place.get("image_url") or "").strip() or None,
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                    "crawled_at": datetime.now(),
                    "review_count": place.get("review_count") if place.get("review_count") is not None else 0,
                    "phone": (place.get("phone") or "").strip() or None,
                    "updated_at": datetime.now(),
                }
                upsert_place(db, payload)
                db_success += 1
            if not args.json_output:
                print(f"\nDB 적재: {db_success}개 (place_id 기준 upsert, 스킵 {db_skip}개)")
        except Exception as e:
            if not args.json_output:
                print(f"\nDB 적재 실패: {e}", file=sys.stderr)
        finally:
            db.close()

    # S3 업로드 (선택적) - 크롤한 결과 전부 한번에 업로드
    if (not args.json_output) and args.s3_bucket and S3_AVAILABLE and results:
        try:
            s3_manager = S3StorageManager(
                bucket_name=args.s3_bucket,
                aws_access_key_id=args.aws_access_key_id or os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=args.aws_secret_access_key or os.getenv("AWS_SECRET_ACCESS_KEY"),
            )

            uploaded_count = 0
            for place in results:
                if not place.get("place_id"):
                    continue
                try:
                    s3_manager.upload_place_raw_data(
                        place_id=str(place.get("place_id")),
                        data=place
                    )
                    uploaded_count += 1
                except Exception as e:
                    if not args.json_output:
                        print(f"S3 업로드 실패 (place_id={place.get('place_id')}): {str(e)}", file=sys.stderr)

            if not args.json_output:
                print(f"S3에 {uploaded_count}개 장소 원본 데이터 업로드 완료")
        except Exception as e:
            if not args.json_output:
                print(f"S3 업로드 초기화 실패: {str(e)}", file=sys.stderr)
    elif args.s3_bucket and not S3_AVAILABLE and results:
        if not args.json_output:
            print("S3 업로드를 위해 s3_storage 모듈이 필요합니다.", file=sys.stderr)

    if args.json_output:
        print(json.dumps(results, ensure_ascii=False))
    else:
        print_results_summary(results)
        print(f"\n크롤한 장소 수: {len(results)}개")


if __name__ == "__main__":
    run_cli()
