"""
DB의 places 를 review_count DESC 로 읽어 네이버 리뷰를 빠르게 크롤 → 요약/임베딩 저장.

scripts/crawl_reviews_from_db.py 의 경량/고속 버전.
- S3 업로드 없음
- 리뷰 jsonl 저장 없음
- __APOLLO_STATE__ 만 노린다 (DOM fallback 단순화)
- 브라우저 1회만 launch, 워커당 page 하나 유지
- 고정 sleep 대신 wait_for_function / 짧은 timeout
- 이미지/미디어/폰트 리소스 차단
- review_count DESC 정렬, 이미 임베딩 있는 장소 자동 스킵
- OpenAI 호출은 asyncio.to_thread 로 병렬화 가능

사용:
  # 기본 (워커 2개, 리뷰수 100개 이상 장소만, 이미 처리된 장소 스킵)
  python scripts/fast_review_summarize.py --min-reviews 100

  # 워커 3개로 강한 병렬, 50개 이상 리뷰만
  python scripts/fast_review_summarize.py --workers 3 --min-reviews 50

  # 테스트로 50개만
  python scripts/fast_review_summarize.py --limit 50 --workers 1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_ROOT / ".env")

sys.path.insert(0, str(BACKEND_ROOT))

from playwright.async_api import async_playwright, BrowserContext, Page  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.place import Place  # noqa: E402
from app.models.place_summary_embedding import PlaceSummaryEmbedding  # noqa: E402
from app.services.recommendation import (  # noqa: E402
    refresh_place_summary_embeddings_from_review_texts,
)


# ===========================
# 크롤 옵션
# ===========================
PAGE_TIMEOUT = 20000  # ms
INITIAL_WAIT_MS = 2200  # goto 직후 대기 (apollo 채워질 시간)
WAIT_APOLLO_TIMEOUT = 8000
WAIT_MORE_TIMEOUT = 3000
MAX_MORE_CLICKS = 12  # 더보기 최대 횟수 (리뷰 ~10개씩 추가)


def _launch_options() -> dict:
    return {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--mute-audio",
            "--hide-scrollbars",
        ],
    }


def _context_options() -> dict:
    return {
        "viewport": {"width": 1920, "height": 1080},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "extra_http_headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
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


async def _block_heavy_resources(context: BrowserContext) -> None:
    """이미지/미디어/폰트만 abort. CSS/JS 는 유지 (더보기 버튼 셀렉터 위해 필요)."""

    async def handler(route):
        try:
            rt = route.request.resource_type
            if rt in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            pass

    await context.route("**/*", handler)


# ===========================
# 리뷰 크롤
# ===========================

EXTRACT_REVIEWS_JS = r"""
() => {
    if (!window.__APOLLO_STATE__) return [];
    const apollo = window.__APOLLO_STATE__;
    const reviews = [];
    const imagesMap = {};

    for (const key in apollo) {
        if (key.startsWith('VisitorImages:')) {
            const im = apollo[key];
            if (im && im.reviewId) imagesMap[im.reviewId] = {
                nickname: im.nickname || '',
            };
        }
    }
    for (const key in apollo) {
        if (key.startsWith('VisitorReviews:')) {
            const data = apollo[key];
            if (!data || (!data.review && !data.reviewId)) continue;
            const reviewId = data.reviewId || key.split(':')[1];
            const html = data.review || '';
            const text = html.replace(/<[^>]*>/g, '').trim();
            const meta = imagesMap[reviewId] || {};
            reviews.push({
                review_id: reviewId,
                content: text,
                author: meta.nickname || '',
            });
        }
    }
    return reviews;
}
"""

WAIT_APOLLO_FN = (
    "() => window.__APOLLO_STATE__ && "
    "Object.keys(window.__APOLLO_STATE__).filter(k => k.startsWith('VisitorReviews:')).length > 0"
)


EXTRACT_REVIEWS_DOM_JS = r"""
() => {
    const items = document.querySelectorAll('ul#_review_list > li.EjjAW');
    const result = [];
    items.forEach((el, idx) => {
        const author = el.querySelector('span.pui__NMi-Dp');
        const content = el.querySelector('div.pui__vn15t2 > a');
        const text = content ? content.innerText.trim() : '';
        if (!text) return;
        result.push({
            review_id: 'dom_' + idx + '_' + (author ? author.innerText : ''),
            content: text,
            author: author ? author.innerText.trim() : '',
        });
    });
    return result;
}
"""


async def _click_more(page: Page) -> bool:
    return await page.evaluate(
        r"""
        () => {
            const sels = ['div.NSTUp a.fvwqf', 'a.fvwqf'];
            for (const s of sels) {
                const el = document.querySelector(s);
                if (el) {
                    el.scrollIntoView();
                    el.click();
                    return true;
                }
            }
            return false;
        }
        """
    )


async def fetch_reviews_fast(page: Page, place_id: int, max_count: int) -> list[str]:
    """리뷰 텍스트(content)만 빠르게 회수. 빈 리스트면 리뷰 없음/실패."""
    url = f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    except Exception:
        return []

    # apollo 가 채워질 시간 확보
    await page.wait_for_timeout(INITIAL_WAIT_MS)

    # __APOLLO_STATE__ 가 (있다면) 리뷰 키 채워질 때까지 대기
    apollo_ready = True
    try:
        await page.wait_for_function(WAIT_APOLLO_FN, timeout=WAIT_APOLLO_TIMEOUT)
    except Exception:
        apollo_ready = False

    if apollo_ready:
        for _ in range(MAX_MORE_CLICKS):
            try:
                current = await page.evaluate(
                    "() => Object.keys(window.__APOLLO_STATE__ || {})"
                    ".filter(k => k.startsWith('VisitorReviews:')).length"
                )
            except Exception:
                current = 0
            if current >= max_count:
                break
            if not await _click_more(page):
                break
            try:
                await page.wait_for_function(
                    f"() => Object.keys(window.__APOLLO_STATE__ || {{}})"
                    f".filter(k => k.startsWith('VisitorReviews:')).length > {current}",
                    timeout=WAIT_MORE_TIMEOUT,
                )
            except Exception:
                break

        try:
            raw = await page.evaluate(EXTRACT_REVIEWS_JS)
        except Exception:
            raw = []
    else:
        raw = []

    # APOLLO 가 비어있거나 추출 실패 → DOM fallback
    if not raw:
        # DOM 에 리뷰가 더 있는지 확인하면서 더보기 클릭
        for _ in range(MAX_MORE_CLICKS):
            try:
                current = await page.evaluate(
                    "() => document.querySelectorAll('ul#_review_list > li.EjjAW').length"
                )
            except Exception:
                current = 0
            if current >= max_count:
                break
            if not await _click_more(page):
                break
            try:
                await page.wait_for_function(
                    f"() => document.querySelectorAll('ul#_review_list > li.EjjAW').length > {current}",
                    timeout=WAIT_MORE_TIMEOUT,
                )
            except Exception:
                break
        try:
            raw = await page.evaluate(EXTRACT_REVIEWS_DOM_JS)
        except Exception:
            raw = []

    seen: set[str] = set()
    texts: list[str] = []
    for r in raw:
        rid = r.get("review_id")
        content = (r.get("content") or "").strip()
        if not content or rid in seen:
            continue
        seen.add(rid)
        texts.append(content)
        if len(texts) >= max_count:
            break
    return texts


# ===========================
# DB / OpenAI 처리
# ===========================

def load_targets(
    skip_done: bool,
    min_reviews: int,
    limit: int | None,
    shard: int = 0,
    shards: int = 1,
    skip_offset: int = 0,
) -> list[tuple[int, str]]:
    """리뷰 많은 순서대로 (place_id, name) 리턴.

    skip_done=True 면 이미 임베딩 있는 행 제외.
    skip_offset>0 이면 정렬된 리스트의 앞 N개 건너뜀.
    shards>1 이면 인덱스 i%shards == shard 인 것만 선택.
    """
    db = SessionLocal()
    try:
        q = db.query(Place.id, Place.name, Place.review_count)
        if min_reviews > 0:
            q = q.filter(Place.review_count >= min_reviews)
        rows = q.order_by(Place.review_count.desc().nulls_last()).all()
        if skip_done:
            done = {
                r[0]
                for r in db.query(PlaceSummaryEmbedding.place_id).distinct().all()
            }
            rows = [r for r in rows if r[0] not in done]
        if skip_offset > 0:
            rows = rows[skip_offset:]
        if shards > 1:
            rows = [r for i, r in enumerate(rows) if i % shards == shard]
        if limit:
            rows = rows[:limit]
        return [(int(r[0]), r[1] or "") for r in rows]
    finally:
        db.close()


def process_one_sync(place_id: int, place_name: str, review_texts: list[str]) -> int:
    """동기 OpenAI 호출 (요약 + 카테고리 + 임베딩) → DB 저장. 삽입 행 수 반환."""
    if not review_texts:
        return 0
    db = SessionLocal()
    try:
        _, _, inserted = refresh_place_summary_embeddings_from_review_texts(
            db=db,
            place_id=place_id,
            review_texts=review_texts,
            place_name=place_name or None,
        )
        return inserted
    except Exception as exc:
        db.rollback()
        print(f"  [FAIL] place_id={place_id}: {exc}", file=sys.stderr)
        return 0
    finally:
        db.close()


# ===========================
# 워커 / 메인
# ===========================

async def worker(
    name: str,
    page: Page,
    queue: asyncio.Queue,
    max_count: int,
    counters: dict,
) -> None:
    while True:
        item = await queue.get()
        try:
            if item is None:
                return
            place_id, place_name, review_count = item
            try:
                texts = await fetch_reviews_fast(page, place_id, max_count)
                if not texts:
                    counters["no_review"] += 1
                    print(
                        f"  [{name}] place_id={place_id} ({place_name}, rc={review_count}) 리뷰 0",
                        file=sys.stderr,
                    )
                    continue
                # OpenAI 호출은 to_thread 로 다른 워커와 병렬 가능
                inserted = await asyncio.to_thread(
                    process_one_sync, place_id, place_name, texts
                )
                if inserted > 0:
                    counters["ok"] += 1
                    counters["embeddings"] += inserted
                    counters["reviews"] += len(texts)
                    print(
                        f"  [{name}] place_id={place_id} ({place_name}): "
                        f"리뷰 {len(texts)} → 임베딩 {inserted}",
                        file=sys.stderr,
                    )
                else:
                    counters["fail"] += 1
            except Exception as exc:
                counters["fail"] += 1
                print(f"  [{name}] place_id={place_id} 예외: {exc}", file=sys.stderr)
        finally:
            queue.task_done()


async def main_async(
    workers: int,
    max_count: int,
    skip_done: bool,
    min_reviews: int,
    limit: int | None,
    shard: int = 0,
    shards: int = 1,
    skip_offset: int = 0,
) -> None:
    targets = load_targets(
        skip_done=skip_done,
        min_reviews=min_reviews,
        limit=limit,
        shard=shard,
        shards=shards,
        skip_offset=skip_offset,
    )
    if not targets:
        print("처리할 장소가 없습니다.")
        return

    shard_msg = f", shard={shard}/{shards}" if shards > 1 else ""
    skip_msg = f", skip={skip_offset}" if skip_offset > 0 else ""
    print(
        f"📋 {len(targets)}개 장소 (review_count DESC, min={min_reviews}, "
        f"skip_done={skip_done}{shard_msg}{skip_msg}), 워커 {workers}개"
    )
    print("=" * 60)

    # review_count 도 같이 (로그용)
    db = SessionLocal()
    try:
        rc_map = dict(
            db.query(Place.id, Place.review_count)
            .filter(Place.id.in_([t[0] for t in targets]))
            .all()
        )
    finally:
        db.close()

    queue: asyncio.Queue = asyncio.Queue()
    for pid, name in targets:
        await queue.put((pid, name, rc_map.get(pid, 0) or 0))
    for _ in range(workers):
        await queue.put(None)

    counters = {"ok": 0, "no_review": 0, "fail": 0, "embeddings": 0, "reviews": 0}

    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_options())
        try:
            tasks = []
            for i in range(workers):
                ctx = await browser.new_context(**_context_options())
                await _block_heavy_resources(ctx)
                page = await ctx.new_page()
                tasks.append(
                    asyncio.create_task(
                        worker(f"w{i}", page, queue, max_count, counters)
                    )
                )
            await asyncio.gather(*tasks)
        finally:
            await browser.close()

    print("=" * 60)
    print(
        f"완료: 성공 {counters['ok']}, 리뷰0 {counters['no_review']}, "
        f"실패 {counters['fail']} | 처리 리뷰 {counters['reviews']}, "
        f"임베딩 {counters['embeddings']}"
    )


async def debug_one(place_id: int, max_count: int) -> None:
    """단일 place_id 로 page 상태 진단."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_options())
        ctx = await browser.new_context(**_context_options())
        await _block_heavy_resources(ctx)
        page = await ctx.new_page()
        url = f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor"
        print(f"[DEBUG] goto {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        except Exception as e:
            print(f"[DEBUG] goto 실패: {e}")
            await browser.close()
            return
        await page.wait_for_timeout(INITIAL_WAIT_MS)

        apollo_keys_sample = await page.evaluate(
            "() => window.__APOLLO_STATE__ ? Object.keys(window.__APOLLO_STATE__).slice(0,30) : null"
        )
        visitor_review_count = await page.evaluate(
            "() => window.__APOLLO_STATE__ ? "
            "Object.keys(window.__APOLLO_STATE__).filter(k=>k.startsWith('VisitorReviews:')).length : 0"
        )
        dom_li_count = await page.evaluate(
            "() => document.querySelectorAll('ul#_review_list > li.EjjAW').length"
        )
        title = await page.title()
        print(f"[DEBUG] title: {title}")
        print(f"[DEBUG] apollo keys (first 30): {apollo_keys_sample}")
        print(f"[DEBUG] VisitorReviews count: {visitor_review_count}")
        print(f"[DEBUG] DOM li.EjjAW count: {dom_li_count}")

        texts = await fetch_reviews_fast(page, place_id, max_count)
        print(f"[DEBUG] fetched texts: {len(texts)}")
        for i, t in enumerate(texts[:3], 1):
            print(f"  [{i}] {t[:80]}")
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="리뷰 빠른 크롤+요약+임베딩")
    parser.add_argument("--workers", type=int, default=2, help="동시 워커 수 (기본 2)")
    parser.add_argument(
        "--max-count", type=int, default=100, help="장소당 최대 리뷰 수 (기본 100)"
    )
    parser.add_argument(
        "--min-reviews",
        type=int,
        default=0,
        help="이 값 미만 review_count 장소는 제외 (기본 0)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="처리 장소 최대 개수"
    )
    parser.add_argument(
        "--no-skip-done",
        action="store_true",
        help="이미 임베딩 있는 장소도 강제 재처리",
    )
    parser.add_argument(
        "--debug-place-id",
        type=int,
        default=None,
        help="단일 place_id 진단 (apollo/DOM 상태 출력)",
    )
    parser.add_argument(
        "--shards",
        type=int,
        default=1,
        help="총 샤드 수 (서버+로컬 동시 실행 시 둘 다 동일 값). 기본 1=분할 안 함",
    )
    parser.add_argument(
        "--shard",
        type=int,
        default=0,
        help="이 프로세스가 처리할 샤드 인덱스 (0..shards-1)",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        help="정렬된 리스트의 앞 N개 건너뜀 (예: --skip 10000 → 1만번째부터)",
    )
    args = parser.parse_args()

    if args.debug_place_id:
        asyncio.run(debug_one(args.debug_place_id, args.max_count))
        return

    if args.shards < 1 or not (0 <= args.shard < args.shards):
        print("--shards >= 1 이고 0 <= --shard < --shards 여야 합니다.", file=sys.stderr)
        sys.exit(2)

    asyncio.run(
        main_async(
            workers=max(1, args.workers),
            max_count=args.max_count,
            skip_done=not args.no_skip_done,
            min_reviews=args.min_reviews,
            limit=args.limit,
            shard=args.shard,
            shards=args.shards,
            skip_offset=max(0, args.skip),
        )
    )


if __name__ == "__main__":
    main()
