"""
네이버 지도 장소 크롤러 (인터랙티브 단독 실행용).

신 UI(2024~)에서 map.naver.com 검색 iframe(`#searchIframe`) 의 script 태그에
이미 전체 장소 JSON 블록이 내장되어 있다 (id / name / roadAddress / x / y).
이 스크립트 JSON을 파싱해서 70개 가까운 풀데이터를 한 번에 가져온다.
부족한 항목은 상세 페이지(pcmap.place.naver.com/place/{id})에서 보강한다.

실행:
    python scripts/naver_crawl_xy.py
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from playwright.async_api import Frame, Page, async_playwright


TIMEOUT = 30000


def _parse_review_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return 0


def _extract_place_blocks_from_text(text: str) -> List[dict]:
    """Script 텍스트에서 {id, name, roadAddress, x, y, ...} 블록을 정규식으로 찾아낸다.

    네이버 신 UI는 React/Apollo 상태 직렬화를 raw HTML script로 내보내는데, 종종 escape된
    JSON 조각이 포함된다. 완전 파싱은 어려워 보이는 명확한 패턴만 잡는다.
    """
    blocks: Dict[str, dict] = {}
    # id 위치를 앵커로 ±400자 윈도우 안에서 name, roadAddress, x, y 를 추출
    for m in re.finditer(r'"id"\s*:\s*"(\d{6,})"', text):
        start = max(0, m.start() - 600)
        end = min(len(text), m.end() + 1200)
        window = text[start:end]
        pid = m.group(1)
        if pid in blocks:
            continue

        name_m = re.search(r'"name"\s*:\s*"([^"\\]+)"', window)
        road_m = re.search(r'"roadAddress"\s*:\s*"([^"\\]+)"', window)
        addr_m = re.search(r'"address"\s*:\s*"([^"\\]+)"', window)
        x_m = re.search(r'"x"\s*:\s*"([0-9.\-]+)"', window)
        y_m = re.search(r'"y"\s*:\s*"([0-9.\-]+)"', window)
        cat_m = re.search(r'"category"\s*:\s*"([^"\\]+)"', window) or re.search(
            r'"businessCategory"\s*:\s*"([^"\\]+)"', window
        )
        review_m = re.search(
            r'"(?:visitorReviewCount|visitorReviewsTotal|totalReviewCount)"\s*:\s*"?(\d[\d,]*)"?',
            window,
        )
        image_m = re.search(r'"(?:imageUrl|thumUrl|thumbnailUrl|mainPhotoUrl)"\s*:\s*"([^"\\]+)"', window)
        # ai_summary 후보 (네이버가 자주 바꾸는 필드명들을 폭넓게)
        summary_m = re.search(
            r'"(?:aiSummary|summary|oneLineSummary|oneSentenceSummary|oneSentenceIntro|microReview|introduction|shortIntroduction)"\s*:\s*"([^"\\]+)"',
            window,
        )

        if not name_m:
            continue
        if not (road_m or addr_m) or not (x_m and y_m):
            # 좌표나 주소 없으면 일단 건너뛰고 detail로 보강 가능
            continue
        try:
            latitude = float(y_m.group(1))
            longitude = float(x_m.group(1))
        except ValueError:
            continue
        road_address = road_m.group(1) if road_m else None
        origin_address = addr_m.group(1) if addr_m else None
        blocks[pid] = {
            "place_id": pid,
            "name": name_m.group(1),
            "category": cat_m.group(1) if cat_m else "",
            "page": 1,
            "road_address": road_address or origin_address,
            "address": road_address or origin_address,
            "origin_address": origin_address or road_address,
            "latitude": latitude,
            "longitude": longitude,
            "review_count": _parse_review_count(review_m.group(1)) if review_m else 0,
            "image_url": image_m.group(1) if image_m else None,
            "ai_summary": summary_m.group(1) if summary_m else None,
        }
    return list(blocks.values())


PLACE_LI_SELECTOR = "li.UEzoS, li.VLTHu"
PLACE_NAME_JS_EVAL = (
    "() => Array.from(document.querySelectorAll('li.UEzoS span.TYaxT, li.VLTHu span.YwYLL'))"
    ".map(e => (e.textContent || '').trim())"
)
PLACE_LI_COUNT_JS = "() => document.querySelectorAll('li.UEzoS, li.VLTHu').length"


async def _scroll_iframe_list(frame: Frame, rounds: int = 12) -> None:
    """리스트가 더 이상 늘지 않을 때까지 스크롤 (UEzoS/VLTHu 두 변종 모두 지원)."""
    prev_count = 0
    stagnant = 0
    for _ in range(rounds):
        try:
            count = await frame.evaluate(PLACE_LI_COUNT_JS)
        except Exception:
            count = 0
        if count == prev_count:
            stagnant += 1
            if stagnant >= 3:
                break
        else:
            stagnant = 0
        prev_count = count
        try:
            await frame.evaluate(
                """() => {
                    const c = document.querySelector('.Ryr1F')
                        || document.querySelector('#_pcmap_list_scroll_container')
                        || document.body;
                    if (c) c.scrollTop = c.scrollHeight;
                    window.scrollTo(0, document.body.scrollHeight);
                }"""
            )
        except Exception:
            pass
        await asyncio.sleep(1.5)


async def _extract_share_data(detail_page: Page) -> dict:
    """상세 페이지의 공유 버튼(a#_btp.share / a.naver-splugin)에서 place_id, 이름, 주소, 이미지를 추출."""
    data = await detail_page.evaluate(
        r"""
        () => {
            const a = document.querySelector('a#_btp\\.share, a.naver-splugin[data-url*="/place/"]');
            if (!a) return null;
            const url = a.getAttribute('data-url') || '';
            const title = a.getAttribute('data-title') || '';
            const image = a.getAttribute('data-kakaotalk-image-url') || '';
            const m = url.match(/\/place\/(\d+)/);
            const lines = title.split('\n').map(s => s.trim()).filter(Boolean);
            return {
                place_id: m ? m[1] : null,
                name: lines[0] || null,
                address: lines[1] || null,
                image_url: image || null,
            };
        }
        """
    )
    return data or {}


async def _extract_address_info(detail_page: Page) -> dict:
    """상세 페이지 __NEXT_DATA__ 에서 좌표/주소/AI요약을 추출."""
    return await detail_page.evaluate(
        r"""
        () => {
            const st = window.__NEXT_DATA__?.props?.pageProps?.initialState;
            const place = st?.place;
            const loc = place?.location;
            if (!place || !loc) return {};

            // 이미지 후보를 넓게 탐색
            const imgCandidates = [
                place.imageUrl, place.thumbnailUrl, place.thumUrl, place.mainPhotoUrl,
            ];
            if (Array.isArray(place.photos)) {
                for (const p of place.photos) {
                    if (!p) continue;
                    if (p.url) imgCandidates.push(p.url);
                    if (p.imageUrl) imgCandidates.push(p.imageUrl);
                    if (p.originUrl) imgCandidates.push(p.originUrl);
                }
            }
            if (Array.isArray(place.images)) {
                for (const i of place.images) {
                    if (typeof i === "string") imgCandidates.push(i);
                    else if (i) {
                        if (i.url) imgCandidates.push(i.url);
                        if (i.imageUrl) imgCandidates.push(i.imageUrl);
                    }
                }
            }
            const imageUrl = imgCandidates.find(
                (v) => typeof v === "string" && v.startsWith("http")
            ) || null;

            // AI 요약 후보도 넓게 탐색
            const summaryCandidates = [
                place.aiSummary,
                place.summary,
                place.oneLineSummary,
                place.oneSentenceSummary,
                place.oneSentenceIntro,
                place.microReview,
                place.introduction,
                place.shortIntroduction,
                st?.place?.microReview,
                st?.place?.summary,
                st?.place?.aiSummary,
                st?.place?.introduction,
            ];
            const aiSummary = summaryCandidates.find(
                (v) => typeof v === "string" && v.trim().length > 0
            ) || null;

            return {
                name: place.name,
                category: place.category || place.businessCategory,
                roadAddress: loc.roadAddress,
                address: loc.address,
                x: loc.x || loc.lng,
                y: loc.y || loc.lat,
                imageUrl,
                aiSummary,
                reviewCount: place.visitorReviewCount || place.visitorReviewsTotal,
            };
        }
        """
    )


async def _extract_ai_summary_from_dom(detail_page: Page) -> Optional[str]:
    """상세 페이지 DOM 에서 'AI 요약' 배지 옆 문구를 fallback으로 추출."""
    try:
        return await detail_page.evaluate(
            r"""
            () => {
                const aiBadge = Array.from(document.querySelectorAll("*"))
                    .find((el) => (el.textContent || "").trim() === "AI 요약");
                if (!aiBadge) return null;
                const containers = [
                    aiBadge.parentElement,
                    aiBadge.closest("div"),
                    aiBadge.closest("li"),
                    aiBadge.closest("section"),
                ].filter(Boolean);
                for (const node of containers) {
                    const text = (node.textContent || "").replace(/\s+/g, " ").trim();
                    if (!text) continue;
                    const cleaned = text.replace("AI 요약", "").trim();
                    if (cleaned && cleaned.length >= 8) return cleaned;
                }
                return null;
            }
            """
        )
    except Exception:
        return None


async def _enrich_place_via_detail(context, place_id: str) -> Optional[dict]:
    """place_id 만 가지고 상세 페이지를 방문해 풀데이터를 채운다."""
    detail = await context.new_page()
    try:
        await detail.goto(f"https://pcmap.place.naver.com/place/{place_id}", wait_until="domcontentloaded", timeout=TIMEOUT)
        await detail.wait_for_timeout(800)
        share = await _extract_share_data(detail)
        nxt = await _extract_address_info(detail)
        road = nxt.get("roadAddress") or share.get("address")
        if not road:
            return None
        x = nxt.get("x")
        y = nxt.get("y")
        if x is None or y is None:
            return None
        try:
            latitude = float(y)
            longitude = float(x)
        except (TypeError, ValueError):
            return None
        ai_summary = nxt.get("aiSummary") or await _extract_ai_summary_from_dom(detail)
        return {
            "place_id": str(place_id),
            "name": (nxt.get("name") or share.get("name") or "").strip() or None,
            "category": nxt.get("category") or "",
            "page": 1,
            "road_address": road,
            "address": road,
            "origin_address": nxt.get("address") or share.get("address") or road,
            "latitude": latitude,
            "longitude": longitude,
            "review_count": _parse_review_count(nxt.get("reviewCount")),
            "image_url": nxt.get("imageUrl") or share.get("image_url"),
            "ai_summary": ai_summary,
        }
    except Exception:
        return None
    finally:
        await detail.close()


async def _enrich_missing_fields(context, place: dict) -> dict:
    """이미 풀데이터가 있지만 image_url 또는 ai_summary 가 비어있는 항목을 상세 페이지로 보강."""
    pid = place.get("place_id")
    if not pid:
        return place
    if place.get("image_url") and place.get("ai_summary"):
        return place
    detail = await context.new_page()
    try:
        await detail.goto(
            f"https://pcmap.place.naver.com/place/{pid}",
            wait_until="domcontentloaded",
            timeout=TIMEOUT,
        )
        await detail.wait_for_timeout(600)
        share = await _extract_share_data(detail)
        nxt = await _extract_address_info(detail)
        if not place.get("image_url"):
            place["image_url"] = nxt.get("imageUrl") or share.get("image_url") or place.get("image_url")
        if not place.get("ai_summary"):
            place["ai_summary"] = nxt.get("aiSummary") or await _extract_ai_summary_from_dom(detail)
    except Exception:
        pass
    finally:
        await detail.close()
    return place


class NaverMapRestaurantCrawler:
    """iframe DOM + script JSON 파싱 기반 장소 크롤러."""

    def __init__(self, headless: bool = True, verbose: bool = True, detail_concurrency: int = 4):
        self.headless = headless
        self.verbose = verbose
        self.detail_concurrency = detail_concurrency
        self.launch_options = self._launch_options()

    def _launch_options(self) -> dict:
        return {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--window-size=1920,1080",
            ],
        }

    def _context_options(self) -> dict:
        return {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
            "locale": "ko-KR",
            "timezone_id": "Asia/Seoul",
        }

    async def _crawl(self, query: str, scroll_rounds: int = 12) -> List[Dict]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**self.launch_options)
            context = await browser.new_context(**self._context_options())
            page = await context.new_page()

            try:
                await page.goto("https://map.naver.com/", wait_until="domcontentloaded", timeout=TIMEOUT)
                inp = await page.wait_for_selector("input.input_search", timeout=TIMEOUT)
                await inp.fill(query)
                await inp.press("Enter")

                try:
                    await page.wait_for_selector("iframe#searchIframe", timeout=TIMEOUT)
                except Exception:
                    print("[WARN] searchIframe 미발견 (검색 결과 없음 / IP 차단 가능)")
                    return []
                el = await page.query_selector("iframe#searchIframe")
                frame = await el.content_frame()
                if not frame:
                    return []
                # iframe 내부 도메인이 완전히 바뀌기 전에 wait_for_selector 가 호출되면 li 가 안 보이는
                # 케이스가 있어 1차 대기는 짧게 잡고, 실패 시 한 번 더 iframe 을 다시 얻어와 재시도한다.
                # UEzoS (map.naver.com 변종) / VLTHu (pcmap.place.naver.com 변종) 둘 다 받는다.
                async def _wait_li(_frame: Frame, timeout_ms: int) -> bool:
                    try:
                        await _frame.wait_for_selector(PLACE_LI_SELECTOR, timeout=timeout_ms)
                        return True
                    except Exception:
                        return False

                if not await _wait_li(frame, 8000):
                    await page.wait_for_timeout(2000)
                    el = await page.query_selector("iframe#searchIframe")
                    frame = await el.content_frame() if el else frame
                    if not frame or not await _wait_li(frame, 12000):
                        body = await frame.evaluate("() => document.body.innerText.slice(0, 400)") if frame else ""
                        url = frame.url if frame else "<no frame>"
                        if "검색 결과가 없습니다" in body or "이용이 제한되었습니다" in body or "잠시 후 다시" in body:
                            print(f"[WARN] 차단/검색결과 없음: {body[:120]}")
                        else:
                            print(f"[WARN] li(UEzoS/VLTHu) 미발견 (url={url}) body[:200]={body[:200].replace(chr(10),' ')}")
                        return []

                await _scroll_iframe_list(frame, rounds=scroll_rounds)

                names = await frame.evaluate(PLACE_NAME_JS_EVAL)
                if self.verbose:
                    print(f"  iframe li(UEzoS/VLTHu) 시인 이름: {len(names)}개")

                script_text = await frame.evaluate(
                    "() => Array.from(document.querySelectorAll('script')).map(s => s.textContent || '').join('\\n')"
                )
                if self.verbose:
                    print(f"  script 텍스트 길이: {len(script_text)}")

                # 1차: script JSON에서 풀데이터 찾기
                blocks = _extract_place_blocks_from_text(script_text)
                if self.verbose:
                    print(f"  script JSON 풀데이터 추출: {len(blocks)}개")

                # 화면에 보이는 이름과 매칭되는 것만 우선 (이름 set 기반)
                name_set = {n.strip() for n in names if n and n.strip()}
                primary = [b for b in blocks if b["name"] in name_set] if name_set else blocks
                if self.verbose:
                    print(f"  화면 이름과 매칭된 풀데이터: {len(primary)}개")

                results: Dict[str, dict] = {b["place_id"]: b for b in primary}

                # 2차: 매칭 안 된 이름은 script id 만 가지고 상세 페이지로 보강
                missing_names = [n for n in names if n and n.strip() and n.strip() not in {b["name"] for b in primary}]
                if missing_names:
                    if self.verbose:
                        print(f"  상세 페이지 보강 필요: {len(missing_names)}개")
                    all_ids = re.findall(r'"id"\s*:\s*"(\d{6,})"', script_text)
                    # 풀데이터에서 못 찾은 id 후보
                    candidate_ids = [pid for pid in dict.fromkeys(all_ids) if pid not in results]
                    sem = asyncio.Semaphore(self.detail_concurrency)

                    async def fetch(pid: str) -> Optional[dict]:
                        async with sem:
                            return await _enrich_place_via_detail(context, pid)

                    extra = await asyncio.gather(*[fetch(pid) for pid in candidate_ids[: len(missing_names) + 10]])
                    for item in extra:
                        if item and item["name"] in name_set and item["place_id"] not in results:
                            results[item["place_id"]] = item

                # 3차: image_url 또는 ai_summary 가 비어있는 항목 상세 페이지 보강
                need_enrich = [
                    p for p in results.values()
                    if not p.get("image_url") or not p.get("ai_summary")
                ]
                if need_enrich:
                    if self.verbose:
                        print(f"  image_url/ai_summary 보강 필요: {len(need_enrich)}개")
                    sem2 = asyncio.Semaphore(self.detail_concurrency)

                    async def enrich(p: dict) -> dict:
                        async with sem2:
                            return await _enrich_missing_fields(context, p)

                    await asyncio.gather(*[enrich(p) for p in need_enrich])

                return list(results.values())
            finally:
                await browser.close()

    async def crawl_single_page(self, search_query: str, page_num: int = 1) -> List[Dict]:
        return await self._crawl(search_query)

    async def crawl_multiple_pages(self, search_query: str, max_pages: int = 5) -> List[Dict]:
        return await self._crawl(search_query, scroll_rounds=max(12, max_pages * 4))


def merge_and_dedupe_results(
    all_results: List[List[Dict]], existing_place_ids: set
) -> List[Dict]:
    seen: set = set(existing_place_ids)
    merged: List[Dict] = []
    for page_results in all_results:
        for item in page_results:
            pid = item.get("place_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            merged.append(item)
    return merged


def print_results_summary(results: List[Dict]) -> None:
    print(f"\n총 {len(results)}개 신규 식당 수집")
    for i, restaurant in enumerate(results, 1):
        place_id = restaurant.get("place_id", "")
        name = restaurant.get("name", "")
        category = restaurant.get("category", "")
        origin_address = restaurant.get("origin_address")
        address = restaurant.get("address")
        latitude = restaurant.get("latitude")
        longitude = restaurant.get("longitude")
        review_count = restaurant.get("review_count")

        image_url = restaurant.get("image_url")
        ai_summary = restaurant.get("ai_summary")

        parts = [
            f"{i}. {place_id} [{name}]",
            f"[{category}]" if category else None,
            f"[origin_address: {origin_address}]" if origin_address else None,
            f"[address: {address}]" if address else None,
            f"[reviews: {review_count}]" if review_count else None,
            (
                f"[latitude: {latitude}, longitude: {longitude}]"
                if latitude is not None and longitude is not None
                else None
            ),
            f"[image_url: {image_url[:60]}...]" if image_url else None,
            f"[ai_summary: {ai_summary[:60]}...]" if ai_summary else None,
        ]
        print(" ".join(p for p in parts if p))


async def main() -> None:
    try:
        search_query = input(
            "식당 크롤링 할 위치를 입력하세요 (공덕역 식당 등등...) : "
        ).strip()
        if not search_query:
            print("검색어가 비어 있어 종료합니다.")
            return
        print(f"search_query: {search_query}")

        max_pages_raw = input("크롤링 깊이 (1=빠름, 5=기본, 클수록 더 스크롤): ").strip()
        max_pages = int(max_pages_raw) if max_pages_raw.isdigit() and int(max_pages_raw) > 0 else 5
        print(f"크롤링 깊이={max_pages}")

        crawler = NaverMapRestaurantCrawler(headless=True)
        all_results = await crawler.crawl_multiple_pages(search_query, max_pages)

        print("\n=== 크롤링 결과 ===")
        print(f"총 {len(all_results)}개 결과 수집됨")
        if all_results:
            print_results_summary(all_results)
        else:
            print("수집된 결과가 없습니다.")
    except KeyboardInterrupt:
        print("\n중단되었습니다.")
    except Exception as e:
        print(f"프로그램 실행 중 오류 발생: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
