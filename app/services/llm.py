"""Utilities for interacting with OpenAI."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.core.config import settings
from app.schemas.review import CategoryInfo


class LLMService:
    """Wrapper around OpenAI APIs for extraction + embedding."""

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        self._client = OpenAI(api_key=settings.openai_api_key)

    def extract_categories(self, text: str) -> CategoryInfo:
        """Extract structured category info from review text via LLM."""
        system_prompt = (
            "너는 한국어 리뷰에서 동행자/메뉴/분위기/모임목적 정보를 추출하는 어시스턴트야. "
            "JSON만 반환하고 값이 없으면 null을 사용해. "
            "모든 필드는 반드시 문자열(string) 타입이어야 하며, 여러 값이 있으면 쉼표로 구분된 하나의 문자열로 반환해. "
            "리스트나 배열 형태로 반환하지 마."
        )
        user_prompt = (
            "리뷰에서 다음 필드를 채워줘:\n"
            "- companion (동행자: 문자열, 여러 명이면 쉼표로 구분)\n"
            "- menu (메뉴: 문자열, 여러 메뉴면 쉼표로 구분, 예: '볶음우동, 치킨가라야케')\n"
            "- mood (분위기: 문자열)\n"
            "- purpose (모임 목적: 문자열)\n\n"
            "중요: 모든 값은 반드시 문자열(string) 타입이어야 합니다. 리스트나 배열을 사용하지 마세요.\n\n"
            f"리뷰: {text}"
        )
        response = self._client.chat.completions.create(
            model=settings.openai_response_model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content
        data: dict[str, Any] = json.loads(content)
        
        # 안전장치: 리스트/숫자/"null" 문자열 등을 정규화
        def normalize_value(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, list):
                # 리스트를 쉼표로 구분된 문자열로 변환
                joined = ", ".join(str(v) for v in value if v)
                s = joined.strip()
            elif isinstance(value, (int, float)):
                s = str(value).strip()
            else:
                s = str(value).strip()
            # 모델이 문자열 "null"/"None"/"없음" 등을 넣는 경우를 빈 값으로 처리
            if not s:
                return None
            lowered = s.lower()
            if lowered in {"null", "none"} or s in {"없음", "없다"}:
                return None
            return s
        
        return CategoryInfo(
            companion=normalize_value(data.get("companion")),
            menu=normalize_value(data.get("menu")),
            mood=normalize_value(data.get("mood")),
            purpose=normalize_value(data.get("purpose")),
        )

    def extract_categories_from_query(self, query: str) -> CategoryInfo:
        """Extract structured category info from user query via LLM."""
        system_prompt = (
            "너는 사용자의 장소 추천 요청에서 동행자/메뉴/분위기/모임목적/업종(place_type) 정보를 추출하는 어시스턴트야. "
            "JSON만 반환하고 값이 없으면 null을 사용해. "
            "모든 필드는 반드시 문자열(string) 타입이어야 하며, 여러 값이 있으면 쉼표로 구분된 하나의 문자열로 반환해. "
            "리스트나 배열 형태로 반환하지 마."
        )
        user_prompt = (
            "사용자 요청에서 다음 필드를 추출해줘:\n"
            "- companion (동행자: 문자열, 예: 친구, 연인, 가족, 혼자 등)\n"
            "- menu (먹고 싶은 메뉴/음식: 문자열. 구체적인 음식·메뉴일 때만 채워줘. 예: 파스타, 스테이크, 라떼, 브런치, 회, 초밥, 치킨, 베이글 등. '한식', '카페', '양식'처럼 장소 종류는 place_type에 넣고 menu에는 넣지 마)\n"
            "- mood (분위기: 문자열, 예: 조용한, 시끌벅적한, 로맨틱한, 편안한 등)\n"
            "- purpose (모임 목적: 문자열, 예: 데이트, 비즈니스, 친목, 회식 등)\n"
            "- place_type (사용자가 원하는 장소의 업종/종류: 문자열 하나만. 예: 카페, 한식, 이탈리아음식, 일식, 중식, 양식, 베이커리, 술집, 호프 등. '카페 추천해줘', '한식당 있어?', '이탈리안 가고 싶어'처럼 구체적인 업종이 있으면 그걸로 채우고, 없으면 null)\n\n"
            "중요: 모든 값은 문자열(string) 타입으로, 리스트나 배열은 사용하지 마세요. place_type은 DB 장소 카테고리(업종)와 매칭하므로 한 단어 또는 짧은 표현(예: 이탈리아음식)으로만 적어줘.\n\n"
            f"사용자 요청: {query}"
        )
        response = self._client.chat.completions.create(
            model=settings.openai_response_model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content
        data: dict[str, Any] = json.loads(content)
        
        # 안전장치: 리스트/숫자/"null" 문자열 등을 정규화
        def normalize_value(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, list):
                joined = ", ".join(str(v) for v in value if v)
                s = joined.strip()
            elif isinstance(value, (int, float)):
                s = str(value).strip()
            else:
                s = str(value).strip()
            if not s:
                return None
            lowered = s.lower()
            if lowered in {"null", "none"} or s in {"없음", "없다"}:
                return None
            return s
        
        return CategoryInfo(
            companion=normalize_value(data.get("companion")),
            menu=normalize_value(data.get("menu")),
            mood=normalize_value(data.get("mood")),
            purpose=normalize_value(data.get("purpose")),
            place_type=normalize_value(data.get("place_type")),
        )

    def extract_location_from_query(self, query: str) -> dict[str, float] | None:
        """자연어 쿼리에서 위치 정보 추출 (위도/경도 또는 지역명)."""
        system_prompt = (
            "너는 사용자의 장소 추천 요청에서 위치 정보를 추출하는 어시스턴트야. "
            "위치 정보가 있으면 JSON으로 반환하고, 없으면 null을 반환해. "
            "지역명이 있으면 해당 지역의 대표적인 위도/경도를 반환해줘. "
            "예: 홍대 -> latitude: 37.5563, longitude: 126.9239"
        )
        user_prompt = (
            "사용자 요청에서 위치 정보를 추출하고, 지역명이면 해당 지역의 위도/경도를 반환해줘:\n"
            "- latitude (위도: 숫자, 지역명이면 해당 지역의 대표 위도)\n"
            "- longitude (경도: 숫자, 지역명이면 해당 지역의 대표 경도)\n"
            "- region (지역명: 문자열, 참고용)\n\n"
            f"사용자 요청: {query}\n\n"
            "지역명 예시:\n"
            "- 홍대: latitude: 37.5563, longitude: 126.9239\n"
            "- 강남: latitude: 37.4979, longitude: 127.0276\n"
            "- 신촌: latitude: 37.5551, longitude: 126.9368\n"
            "- 이태원: latitude: 37.5345, longitude: 126.9947"
        )
        response = self._client.chat.completions.create(
            model=settings.openai_response_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content
        data: dict[str, Any] = json.loads(content)
        
        # 위도/경도가 있으면 반환
        if "latitude" in data and "longitude" in data:
            try:
                return {
                    "latitude": float(data["latitude"]),
                    "longitude": float(data["longitude"]),
                }
            except (ValueError, TypeError):
                pass
        
        # 지역명만 있으면 None 반환 (Spring에서 중간지점 계산하도록)
        return None

    def summarize_reviews(self, reviews: list[str], place_name: str | None = None) -> str:
        """여러 리뷰를 카테고리 추출 친화적인 단일 요약문으로 생성."""
        cleaned_reviews = [r.strip() for r in reviews if r and r.strip()]
        if not cleaned_reviews:
            return ""

        max_reviews = 30
        max_chars_per_review = 220
        clipped = [r[:max_chars_per_review] for r in cleaned_reviews[:max_reviews]]
        context = "\n".join(f"- {line}" for line in clipped)
        place_info = f"장소명: {place_name}\n" if place_name else ""

        system_prompt = (
            "너는 여러 사용자 리뷰를 1개의 대표 리뷰로 압축 요약하는 어시스턴트야. "
            "추천 시스템에서 카테고리(companion/menu/mood/purpose)를 잘 추출할 수 있게 "
            "핵심 키워드를 빠뜨리지 말고 한국어로 요약해."
        )
        user_prompt = (
            f"{place_info}"
            "아래 리뷰들을 바탕으로 단일 요약 리뷰를 작성해줘.\n"
            "조건:\n"
            "1) 250~700자 사이의 자연스러운 한국어 문단 1개\n"
            "2) 동행자, 메뉴, 분위기, 방문목적 관련 단서를 최대한 포함\n"
            "3) 긍정/부정 포인트를 균형 있게 포함\n"
            "4) 없는 사실을 만들지 말 것\n\n"
            f"리뷰 목록:\n{context}"
        )

        response = self._client.chat.completions.create(
            model=settings.openai_response_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip()

    def embed_text(self, text: str) -> list[float]:
        """Return OpenAI embedding vector."""
        result = self._client.embeddings.create(
            model=settings.openai_embedding_model,
            input=text,
        )
        return result.data[0].embedding


_llm_service_instance: LLMService | None = None


def get_llm_service() -> LLMService:
    """Lazy initialization of LLM service."""
    global _llm_service_instance
    if _llm_service_instance is None:
        _llm_service_instance = LLMService()
    return _llm_service_instance


# 하위 호환성을 위한 모듈 레벨 변수 (lazy)
class _LazyLLMService:
    """Lazy wrapper for LLM service."""
    def __getattr__(self, name):
        return getattr(get_llm_service(), name)


llm_service = _LazyLLMService()


