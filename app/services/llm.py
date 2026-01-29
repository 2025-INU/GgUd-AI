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
        
        # 안전장치: 리스트가 들어왔을 때 문자열로 변환
        def normalize_value(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, list):
                # 리스트를 쉼표로 구분된 문자열로 변환
                return ", ".join(str(v) for v in value if v)
            if isinstance(value, (int, float)):
                return str(value)
            return str(value) if value else None
        
        return CategoryInfo(
            companion=normalize_value(data.get("companion")),
            menu=normalize_value(data.get("menu")),
            mood=normalize_value(data.get("mood")),
            purpose=normalize_value(data.get("purpose")),
        )

    def extract_categories_from_query(self, query: str) -> CategoryInfo:
        """Extract structured category info from user query via LLM."""
        system_prompt = (
            "너는 사용자의 장소 추천 요청에서 동행자/메뉴/분위기/모임목적 정보를 추출하는 어시스턴트야. "
            "JSON만 반환하고 값이 없으면 null을 사용해. "
            "모든 필드는 반드시 문자열(string) 타입이어야 하며, 여러 값이 있으면 쉼표로 구분된 하나의 문자열로 반환해. "
            "리스트나 배열 형태로 반환하지 마."
        )
        user_prompt = (
            "사용자 요청에서 다음 필드를 추출해줘:\n"
            "- companion (동행자: 문자열, 예: 친구, 연인, 가족, 혼자 등)\n"
            "- menu (메뉴: 문자열, 예: 한식, 양식, 카페, 디저트 등)\n"
            "- mood (분위기: 문자열, 예: 조용한, 시끌벅적한, 로맨틱한, 편안한 등)\n"
            "- purpose (모임 목적: 문자열, 예: 데이트, 비즈니스, 친목, 회식 등)\n\n"
            "중요: 모든 값은 반드시 문자열(string) 타입이어야 합니다. 리스트나 배열을 사용하지 마세요.\n\n"
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
        
        # 안전장치: 리스트가 들어왔을 때 문자열로 변환
        def normalize_value(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, list):
                # 리스트를 쉼표로 구분된 문자열로 변환
                return ", ".join(str(v) for v in value if v)
            if isinstance(value, (int, float)):
                return str(value)
            return str(value) if value else None
        
        return CategoryInfo(
            companion=normalize_value(data.get("companion")),
            menu=normalize_value(data.get("menu")),
            mood=normalize_value(data.get("mood")),
            purpose=normalize_value(data.get("purpose")),
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


