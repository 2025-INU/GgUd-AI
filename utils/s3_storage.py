"""S3 storage manager for crawling data.

- places/raw/YYYYMMDD/{query}/{place_id}.json  (query optional)
- reviews/{place_id}/reviews.json (+ optional YYYYMMDD backup)
- images/places/{place_id}/{image_name}
- images/reviews/{review_id}/{image_name}
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


class S3StorageManager:
    """S3에 크롤링 데이터 저장"""

    def __init__(
        self,
        bucket_name: str,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region: str = "ap-northeast-2",
    ) -> None:
        self.bucket_name = bucket_name
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region,
        )

    @staticmethod
    def _safe_prefix(value: str, max_len: int = 50) -> str:
        # 파일/키 경로에 안전한 형태로 변환 (공백/특수문자 → _)
        safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)
        return safe[:max_len] if len(safe) > max_len else safe

    def upload_place_raw_data(self, place_id: str, data: Dict, query: Optional[str] = None) -> str:
        """장소 원본 크롤링 데이터를 S3에 업로드"""
        date_str = datetime.now().strftime("%Y%m%d")
        if query:
            safe_query = self._safe_prefix(query)
            key = f"places/raw/{date_str}/{safe_query}/{place_id}.json"
        else:
            key = f"places/raw/{date_str}/{place_id}.json"

        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        return key

    def upload_reviews(self, place_id: str, reviews: List[Dict], backup: bool = False) -> str:
        """리뷰 데이터를 S3에 업로드
        
        Args:
            place_id: 장소 ID
            reviews: 리뷰 리스트
            backup: True면 날짜별 백업도 생성 (기본값: False, 중복 저장 방지)
        """
        # 최신 데이터 업로드
        key = f"reviews/{place_id}/reviews.json"
        body = json.dumps(reviews, ensure_ascii=False, indent=2).encode("utf-8")
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=body,
            ContentType="application/json",
        )

        # 날짜별 백업 (선택적, 기본값은 False로 변경하여 중복 저장 방지)
        if backup:
            date_str = datetime.now().strftime("%Y%m%d")
            backup_key = f"reviews/{place_id}/{date_str}_reviews.json"
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=backup_key,
                Body=body,
                ContentType="application/json",
            )

        return key

    def upload_place_image(self, place_id: str, image_name: str, image_data: bytes) -> str:
        """장소 이미지를 S3에 업로드"""
        key = f"images/places/{place_id}/{image_name}"
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=image_data,
            ContentType="image/jpeg",
        )
        return key

    def upload_review_image(self, review_id: str, image_name: str, image_data: bytes) -> str:
        """리뷰 이미지를 S3에 업로드"""
        key = f"images/reviews/{review_id}/{image_name}"
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=image_data,
            ContentType="image/jpeg",
        )
        return key

    # 하위 호환성
    def upload_image(self, place_id: str, image_name: str, image_data: bytes) -> str:
        return self.upload_place_image(place_id, image_name, image_data)

    def check_place_exists(self, place_id: str, query: Optional[str] = None) -> bool:
        """S3에 해당 장소 데이터가 이미 있는지 확인"""
        date_str = datetime.now().strftime("%Y%m%d")
        if query:
            safe_query = self._safe_prefix(query)
            key = f"places/raw/{date_str}/{safe_query}/{place_id}.json"
        else:
            key = f"places/raw/{date_str}/{place_id}.json"
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError:
            return False
