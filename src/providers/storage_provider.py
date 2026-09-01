from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from src.config.config_manager import AppConfig
from src.repositories.media_asset_repository import MediaAssetRecord


class StorageProviderError(RuntimeError):
    """媒体存储上传失败。"""


@dataclass(frozen=True)
class StorageUploadResult:
    """媒体资产上传后的结果。"""

    remote_url: str
    object_key: str
    storage_provider: str
    metadata: dict[str, Any]


class StorageProvider(Protocol):
    """媒体存储 Provider 协议。"""

    def can_upload(self) -> bool:
        """当前配置是否足以执行上传。"""

    def unavailable_reason(self) -> str:
        """不能上传时的原因。"""

    def upload(self, asset: MediaAssetRecord) -> StorageUploadResult:
        """上传媒体资产并返回公网 URL。"""


class LocalPublicStorageProvider:
    """开发期本地公开目录 Provider。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def can_upload(self) -> bool:
        """只有配置了 public base URL 时，本地公开目录才有公网意义。"""
        return bool(self._public_base_url())

    def unavailable_reason(self) -> str:
        """返回本地 Provider 不能上传的原因。"""
        if not self._public_base_url():
            return f"{self.config.storage_local_public_base_url_env} 未配置"
        return ""

    def upload(self, asset: MediaAssetRecord) -> StorageUploadResult:
        """复制本地文件到公开目录，并生成 remote_url。"""
        public_base_url = self._public_base_url()
        if not public_base_url:
            raise StorageProviderError(self.unavailable_reason())

        source_path = self._resolve_source_path(asset.path)
        if not source_path.exists():
            raise StorageProviderError(f"媒体本地文件不存在：{source_path}")
        if not source_path.is_file():
            raise StorageProviderError(f"媒体本地路径不是文件：{source_path}")

        object_key = self._build_object_key(asset=asset, source_path=source_path)
        target_path = self.config.storage_local_upload_dir / object_key
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

        normalized_object_key = object_key.replace("\\", "/")
        remote_url = f"{public_base_url.rstrip('/')}/{normalized_object_key}"
        return StorageUploadResult(
            remote_url=remote_url,
            object_key=normalized_object_key,
            storage_provider="local",
            metadata={
                "local_public_path": str(target_path),
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _public_base_url(self) -> str:
        """读取本地公开目录对应的公网 base URL。"""
        return os.getenv(self.config.storage_local_public_base_url_env, "").strip()

    def _resolve_source_path(self, raw_path: str) -> Path:
        """解析媒体资产本地路径。"""
        source_path = Path(raw_path)
        if source_path.is_absolute():
            return source_path
        return self.config.project_root / source_path

    def _build_object_key(self, asset: MediaAssetRecord, source_path: Path) -> str:
        """构造对象存储 key。"""
        content_part = str(asset.content_id) if asset.content_id is not None else "global"
        filename = source_path.name
        return f"{asset.asset_type}/{content_part}/{asset.id}_{filename}"


class CloudflareR2StorageProvider:
    """Cloudflare R2 存储 Provider，使用 S3-compatible API。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def can_upload(self) -> bool:
        """检查 R2 上传所需配置是否齐全。"""
        return not bool(self._missing_config_names())

    def unavailable_reason(self) -> str:
        """返回 R2 Provider 不可用原因。"""
        missing_names = self._missing_config_names()
        if missing_names:
            return "Cloudflare R2 配置缺失：" + ", ".join(missing_names)
        return ""

    def upload(self, asset: MediaAssetRecord) -> StorageUploadResult:
        """上传本地媒体文件到 Cloudflare R2。"""
        missing_names = self._missing_config_names()
        if missing_names:
            raise StorageProviderError(self.unavailable_reason())

        source_path = self._resolve_source_path(asset.path)
        if not source_path.exists():
            raise StorageProviderError(f"媒体本地文件不存在：{source_path}")
        if not source_path.is_file():
            raise StorageProviderError(f"媒体本地路径不是文件：{source_path}")

        object_key = self._build_object_key(asset=asset, source_path=source_path)
        normalized_object_key = object_key.replace("\\", "/")
        bucket = self._env(self.config.storage_r2_bucket_env)
        public_base_url = self._env(self.config.storage_r2_public_base_url_env)
        client = self._create_client()
        extra_args: dict[str, Any] = {}
        if asset.mime_type:
            extra_args["ContentType"] = asset.mime_type

        try:
            client.upload_file(
                Filename=str(source_path),
                Bucket=bucket,
                Key=normalized_object_key,
                ExtraArgs=extra_args or None,
            )
        except Exception as exc:
            raise StorageProviderError(f"Cloudflare R2 上传失败：{exc}") from exc

        remote_url = f"{public_base_url.rstrip('/')}/{normalized_object_key}"
        return StorageUploadResult(
            remote_url=remote_url,
            object_key=normalized_object_key,
            storage_provider="r2",
            metadata={
                "r2_bucket": bucket,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _create_client(self) -> Any:
        """创建 Cloudflare R2 S3 client。"""
        try:
            import boto3
        except ImportError as exc:
            raise StorageProviderError("缺少 boto3 依赖，请先执行 pip install -r requirements.txt") from exc

        account_id = self._env(self.config.storage_r2_account_id_env)
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self._env(self.config.storage_r2_access_key_id_env),
            aws_secret_access_key=self._env(self.config.storage_r2_secret_access_key_env),
            region_name="auto",
        )

    def _missing_config_names(self) -> list[str]:
        """列出缺失的 R2 环境变量名。"""
        required_env_names = [
            self.config.storage_r2_account_id_env,
            self.config.storage_r2_access_key_id_env,
            self.config.storage_r2_secret_access_key_env,
            self.config.storage_r2_bucket_env,
            self.config.storage_r2_public_base_url_env,
        ]
        return [name for name in required_env_names if not self._env(name)]

    def _env(self, name: str) -> str:
        """读取环境变量。"""
        return os.getenv(name, "").strip()

    def _resolve_source_path(self, raw_path: str) -> Path:
        """解析媒体资产本地路径。"""
        source_path = Path(raw_path)
        if source_path.is_absolute():
            return source_path
        return self.config.project_root / source_path

    def _build_object_key(self, asset: MediaAssetRecord, source_path: Path) -> str:
        """构造 R2 对象 key。"""
        content_part = str(asset.content_id) if asset.content_id is not None else "global"
        filename = source_path.name
        return f"{asset.asset_type}/{content_part}/{asset.id}_{filename}"


def create_storage_provider(config: AppConfig) -> StorageProvider:
    """根据配置创建媒体存储 Provider。"""
    provider = config.storage_provider
    if provider == "local":
        return LocalPublicStorageProvider(config=config)
    if provider == "r2":
        return CloudflareR2StorageProvider(config=config)
    if provider == "tos":
        raise StorageProviderError("TOS Provider 尚未实现：请先确认火山 TOS 作为正式云存储")
    if provider == "cos":
        raise StorageProviderError("COS Provider 尚未实现：请先确认腾讯 COS 作为正式云存储")
    raise StorageProviderError(f"不支持的 storage.provider：{provider}")
