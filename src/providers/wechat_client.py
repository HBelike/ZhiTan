from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from src.config.config_manager import AppConfig


class WechatApiError(RuntimeError):
    """微信公众号 API 调用失败。"""


@dataclass(frozen=True)
class WechatAccessToken:
    """微信 access_token 响应。"""

    access_token: str
    expires_in: int


@dataclass(frozen=True)
class WechatUploadedImage:
    """图文正文图片上传结果。"""

    url: str
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class WechatMaterial:
    """永久素材上传结果。"""

    media_id: str
    url: str | None
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class WechatDraft:
    """草稿创建结果。"""

    media_id: str
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class WechatPublish:
    """微信公众号发布提交结果。"""

    publish_id: str
    msg_data_id: str | None
    raw_response: dict[str, Any]


class WechatClient:
    """微信公众号 HTTP API 客户端。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.wechat_config = self._wechat_config(config)
        self.timeout_seconds = float(self.wechat_config.get("timeout_seconds", 30))
        self.api_base_url = str(self.wechat_config.get("api_base_url", "https://api.weixin.qq.com")).rstrip("/") + "/"
        self.app_id_env = str(self.wechat_config.get("app_id_env", "WECHAT_APP_ID"))
        self.app_secret_env = str(self.wechat_config.get("app_secret_env", "WECHAT_APP_SECRET"))

    def has_credentials(self) -> bool:
        """判断微信 AppID 和 AppSecret 是否已经配置。"""
        return bool(os.getenv(self.app_id_env)) and bool(os.getenv(self.app_secret_env))

    def get_access_token(self) -> WechatAccessToken:
        """获取微信公众号 access_token。"""
        app_id = os.getenv(self.app_id_env, "").strip()
        app_secret = os.getenv(self.app_secret_env, "").strip()
        if not app_id or not app_secret:
            raise WechatApiError("WECHAT_APP_ID 或 WECHAT_APP_SECRET 未配置")

        endpoint = self._endpoint("token_endpoint", "/cgi-bin/token")
        response = requests.get(
            endpoint,
            params={
                "grant_type": "client_credential",
                "appid": app_id,
                "secret": app_secret,
            },
            timeout=self.timeout_seconds,
        )
        payload = self._parse_json_response(response)
        self._raise_if_wechat_error(payload, action="获取 access_token")

        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise WechatApiError("微信 access_token 响应缺少 access_token")
        return WechatAccessToken(
            access_token=access_token,
            expires_in=int(payload.get("expires_in", 0)),
        )

    def upload_article_image(self, access_token: str, image_path: Path) -> WechatUploadedImage:
        """上传图文消息正文图片，返回可写入正文 HTML 的微信图片 URL。"""
        payload = self._upload_file(
            access_token=access_token,
            endpoint_key="upload_image_endpoint",
            default_endpoint="/cgi-bin/media/uploadimg",
            file_path=image_path,
            params={},
            data=None,
            action="上传图文正文图片",
        )
        image_url = str(payload.get("url", "")).strip()
        if not image_url:
            raise WechatApiError("上传图文正文图片响应缺少 url")
        return WechatUploadedImage(url=image_url, raw_response=payload)

    def add_permanent_material(
        self,
        access_token: str,
        material_type: str,
        file_path: Path,
        title: str | None = None,
        introduction: str | None = None,
    ) -> WechatMaterial:
        """上传永久素材，图片用于封面，视频用于素材库留存。"""
        data: dict[str, str] | None = None
        if material_type == "video":
            data = {
                "description": json.dumps(
                    {
                        "title": title or file_path.stem,
                        "introduction": introduction or title or file_path.stem,
                    },
                    ensure_ascii=False,
                )
            }

        payload = self._upload_file(
            access_token=access_token,
            endpoint_key="add_material_endpoint",
            default_endpoint="/cgi-bin/material/add_material",
            file_path=file_path,
            params={"type": material_type},
            data=data,
            action=f"上传永久素材 type={material_type}",
        )
        media_id = str(payload.get("media_id", "")).strip()
        if not media_id:
            raise WechatApiError(f"上传永久素材响应缺少 media_id：type={material_type}")
        material_url = str(payload.get("url", "")).strip() or None
        return WechatMaterial(media_id=media_id, url=material_url, raw_response=payload)

    def add_draft(self, access_token: str, article: dict[str, Any]) -> WechatDraft:
        """创建微信公众号草稿。"""
        endpoint = self._endpoint("add_endpoint", "/cgi-bin/draft/add")
        request_body = json.dumps({"articles": [article]}, ensure_ascii=False).encode("utf-8")
        response = requests.post(
            endpoint,
            params={"access_token": access_token},
            data=request_body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=self.timeout_seconds,
        )
        payload = self._parse_json_response(response)
        self._raise_if_wechat_error(payload, action="新增草稿")

        media_id = str(payload.get("media_id", "")).strip()
        if not media_id:
            raise WechatApiError("新增草稿响应缺少 media_id")
        return WechatDraft(media_id=media_id, raw_response=payload)

    def publish_draft(self, access_token: str, media_id: str) -> WechatPublish:
        """提交草稿发布。"""
        normalized_media_id = media_id.strip()
        if not normalized_media_id:
            raise WechatApiError("发布草稿失败：media_id 不能为空")

        endpoint = self._endpoint("publish_submit_endpoint", "/cgi-bin/freepublish/submit")
        response = requests.post(
            endpoint,
            params={"access_token": access_token},
            json={"media_id": normalized_media_id},
            timeout=self.timeout_seconds,
        )
        payload = self._parse_json_response(response)
        self._raise_if_wechat_error(payload, action="提交发布")

        publish_id = str(payload.get("publish_id", "")).strip()
        if not publish_id:
            raise WechatApiError("提交发布响应缺少 publish_id")
        msg_data_id = str(payload.get("msg_data_id", "")).strip() or None
        return WechatPublish(
            publish_id=publish_id,
            msg_data_id=msg_data_id,
            raw_response=payload,
        )

    def get_publish_status(self, access_token: str, publish_id: str) -> dict[str, Any]:
        """查询草稿发布状态。"""
        normalized_publish_id = publish_id.strip()
        if not normalized_publish_id:
            raise WechatApiError("查询发布状态失败：publish_id 不能为空")

        endpoint = self._endpoint("publish_status_endpoint", "/cgi-bin/freepublish/get")
        response = requests.post(
            endpoint,
            params={"access_token": access_token},
            json={"publish_id": normalized_publish_id},
            timeout=self.timeout_seconds,
        )
        payload = self._parse_json_response(response)
        self._raise_if_wechat_error(payload, action="查询发布状态")
        return payload

    def _upload_file(
        self,
        access_token: str,
        endpoint_key: str,
        default_endpoint: str,
        file_path: Path,
        params: dict[str, str],
        data: dict[str, str] | None,
        action: str,
    ) -> dict[str, Any]:
        """执行微信 multipart 文件上传请求。"""
        if not file_path.exists() or not file_path.is_file():
            raise WechatApiError(f"{action}失败：文件不存在 {file_path}")

        endpoint = self._endpoint(endpoint_key, default_endpoint)
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        request_params = {"access_token": access_token, **params}
        with file_path.open("rb") as file:
            response = requests.post(
                endpoint,
                params=request_params,
                files={"media": (file_path.name, file, content_type)},
                data=data,
                timeout=self.timeout_seconds,
            )

        payload = self._parse_json_response(response)
        self._raise_if_wechat_error(payload, action=action)
        return payload

    def _parse_json_response(self, response: requests.Response) -> dict[str, Any]:
        """解析微信 JSON 响应，并把 HTTP 错误转成业务异常。"""
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise WechatApiError(f"微信 HTTP 请求失败：status={response.status_code}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise WechatApiError("微信响应不是合法 JSON") from exc

        if not isinstance(payload, dict):
            raise WechatApiError("微信响应 JSON 不是对象")
        return payload

    def _raise_if_wechat_error(self, payload: dict[str, Any], action: str) -> None:
        """识别微信业务错误码。"""
        errcode = int(payload.get("errcode", 0) or 0)
        if errcode == 0:
            return
        errmsg = str(payload.get("errmsg", "")).strip()
        raise WechatApiError(f"{action}失败：errcode={errcode} errmsg={errmsg}")

    def _endpoint(self, endpoint_key: str, default_endpoint: str) -> str:
        """从配置读取 endpoint 并拼接完整 URL。"""
        draft_config = self.wechat_config.get("draft", {})
        if not isinstance(draft_config, dict):
            draft_config = {}
        endpoint_path = str(draft_config.get(endpoint_key, default_endpoint)).lstrip("/")
        return urljoin(self.api_base_url, endpoint_path)

    def _wechat_config(self, config: AppConfig) -> dict[str, Any]:
        """读取 wechat 配置段。"""
        raw_wechat_config = config.raw.get("wechat", {})
        if not isinstance(raw_wechat_config, dict):
            return {}
        return raw_wechat_config
