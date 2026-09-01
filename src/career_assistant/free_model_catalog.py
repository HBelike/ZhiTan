"""求职助手的官方免费模型目录。

目录只记录公开、非敏感的服务商接入信息。真正的 API Key 始终由模型连接仓储或
部署环境保存；目录本身不会制造匿名调用，也不会把平台 Key 返回给浏览器。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.career_assistant.model_gateway import (
    ModelProfileAvailability,
    ModelReadiness,
)
from src.career_assistant.persistence.model_profile_repository import ModelCostTier


@dataclass(frozen=True)
class FreeModelTemplate:
    """一个可直接填入连接表单的官方模型候选项。

    ``model_id`` 是服务商当前文档中公开的调用标识；服务商可能调整可用目录，
    管理员仍应以其控制台的实时模型列表为准。
    """

    model_id: str
    display_name: str
    supports_vision: bool = False


@dataclass(frozen=True)
class FreeModelProvider:
    """可接入平台的免费额度服务商定义。

    所有云端服务商都要求平台管理员拥有自己的 API Key。管理员保存并验证连接后，
    平台访客可复用该连接，不需要在浏览器侧配置或看到 Key。
    """

    provider_key: str
    display_name: str
    api_base_url: str
    website_url: str
    setup_url: str
    documentation_url: str
    pricing_url: str
    free_label: str
    free_description: str
    templates: tuple[FreeModelTemplate, ...]


FREE_MODEL_PROVIDERS: tuple[FreeModelProvider, ...] = (
    FreeModelProvider(
        provider_key="gemini",
        display_name="Google Gemini",
        api_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        website_url="https://aistudio.google.com",
        setup_url="https://aistudio.google.com/app/apikey",
        documentation_url="https://ai.google.dev/gemini-api/docs/openai",
        pricing_url="https://ai.google.dev/gemini-api/docs/pricing",
        free_label="免费层",
        free_description=(
            "Gemini Developer API 的标准 Free Tier 对部分模型免收输入与输出费用；"
            "可用地区、项目级限额和模型限额以 AI Studio 为准，免费层内容可能用于改进 Google 产品。"
        ),
        templates=(
            FreeModelTemplate("gemini-3.7-flash", "Gemini 3.7 Flash", supports_vision=True),
            FreeModelTemplate("gemini-3.6-flash", "Gemini 3.6 Flash", supports_vision=True),
        ),
    ),
    FreeModelProvider(
        provider_key="groq",
        display_name="Groq",
        api_base_url="https://api.groq.com/openai/v1",
        website_url="https://console.groq.com",
        setup_url="https://console.groq.com/keys",
        documentation_url="https://console.groq.com/docs/models",
        pricing_url="https://console.groq.com/docs/rate-limits",
        free_label="免费层",
        free_description=(
            "Groq Free Plan 对部分生产模型提供按 RPM、RPD、TPM 和 TPD 计算的免费调用额度；"
            "限额按组织共享，并会随模型和账号状态调整，以控制台 Limits 页面为准。"
        ),
        templates=(
            FreeModelTemplate("openai/gpt-oss-120b", "GPT-OSS 120B"),
            FreeModelTemplate("openai/gpt-oss-20b", "GPT-OSS 20B"),
        ),
    ),
    FreeModelProvider(
        provider_key="mistral",
        display_name="Mistral AI",
        api_base_url="https://api.mistral.ai/v1",
        website_url="https://console.mistral.ai",
        setup_url="https://console.mistral.ai/api-keys",
        documentation_url=(
            "https://docs.mistral.ai/getting-started/quickstarts/studio/"
            "activate-and-generate-api-key"
        ),
        pricing_url="https://docs.mistral.ai/admin/billing-usage/usage-limits",
        free_label="免费模式",
        free_description=(
            "Mistral Studio Free mode 无需信用卡即可创建 API Key，并按账号获得有限的月度用量；"
            "具体可用模型和限额以 Studio 的 Limits 页面为准。"
        ),
        templates=(
            FreeModelTemplate("mistral-small-latest", "Mistral Small（最新稳定版）"),
        ),
    ),
    FreeModelProvider(
        provider_key="qwen",
        display_name="阿里云百炼 Qwen",
        api_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        website_url="https://bailian.console.aliyun.com",
        setup_url="https://bailian.console.aliyun.com/cn-beijing/?tab=app#/api-key",
        documentation_url="https://help.aliyun.com/zh/model-studio/new-free-quota/",
        pricing_url="https://help.aliyun.com/zh/model-studio/model-pricing",
        free_label="新用户免费额度",
        free_description=(
            "百炼在中国（北京）为符合条件的新用户按模型发放通常 90 天有效的免费额度；"
            "请使用通用 API Key 并在控制台开启“仅使用免费额度”，到期、耗尽或不符合地域条件后不可继续免费调用。"
        ),
        templates=(
            # 此通用 Vision-Language 模型可作为管理员另行保存的会话模型；
            # 平台自动图片解析由独立 CloudVision 服务调用，不依赖此目录的选择。
            FreeModelTemplate(
                "qwen3.6-flash",
                "Qwen 3.6 Flash（通用图文理解）",
                supports_vision=True,
            ),
            FreeModelTemplate(
                "qwen3.7-plus",
                "Qwen 3.7 Plus（高精度图文理解）",
                supports_vision=True,
            ),
        ),
    ),
    FreeModelProvider(
        provider_key="openrouter",
        display_name="OpenRouter",
        api_base_url="https://openrouter.ai/api/v1",
        website_url="https://openrouter.ai",
        setup_url="https://openrouter.ai/settings/keys",
        documentation_url="https://openrouter.ai/docs/guides/routing/routers/free-router",
        pricing_url="https://openrouter.ai/pricing",
        free_label="免费模型路由",
        free_description=(
            "未购买额度的账号每天最多 50 次免费模型请求、20 RPM；累计购买至少 10 美元额度后"
            "上限为每天 1,000 次。openrouter/free 会动态路由，不保证固定模型。"
        ),
        templates=(
            FreeModelTemplate("openrouter/free", "OpenRouter Free 自动路由"),
        ),
    ),
    FreeModelProvider(
        provider_key="modelscope",
        display_name="ModelScope 魔搭",
        api_base_url="https://api-inference.modelscope.cn/v1",
        website_url="https://www.modelscope.cn/home?tab=model",
        setup_url="https://modelscope.cn/my/myaccesstoken",
        documentation_url="https://modelscope.cn/docs/model-service/API-Inference/intro",
        pricing_url="https://modelscope.cn/docs/model-service/API-Inference/limits",
        free_label="魔力值体验",
        free_description=(
            "API Inference 通过魔力值兑换免费体验调用；账号需关联已实名认证的阿里云账号。"
            "模型范围、单次消耗和并发会动态调整，仅列出模型页仍提供 API-Inference 示例的候选项。"
        ),
        templates=(
            FreeModelTemplate("deepseek-ai/DeepSeek-V4-Pro", "DeepSeek V4 Pro"),
            FreeModelTemplate("ZhipuAI/GLM-5.2", "GLM 5.2"),
        ),
    ),
    FreeModelProvider(
        provider_key="siliconflow",
        display_name="硅基流动 SiliconFlow",
        api_base_url="https://api.siliconflow.cn/v1",
        website_url="https://cloud.siliconflow.cn",
        setup_url="https://cloud.siliconflow.cn/account/ak",
        documentation_url="https://docs.siliconflow.cn/cn/userguide/quickstart",
        pricing_url="https://siliconflow.cn/pricing",
        free_label="当前免费模型",
        free_description=(
            "只列出硅基流动价格页当前明确将输入和输出都标记为免费的文本模型；"
            "价格和准入可能调整，保存前仍应在控制台再次核对。"
        ),
        templates=(
            FreeModelTemplate("THUDM/GLM-Z1-9B-0414", "GLM-Z1 9B"),
        ),
    ),
    FreeModelProvider(
        provider_key="nvidia",
        display_name="NVIDIA NIM",
        api_base_url="https://integrate.api.nvidia.com/v1",
        website_url="https://build.nvidia.com",
        setup_url="https://build.nvidia.com/settings/api-keys",
        documentation_url="https://docs.api.nvidia.com/nim/reference/llm-apis",
        pricing_url="https://build.nvidia.com/explore/discover?api-key=true",
        free_label="开发试用",
        free_description=(
            "NVIDIA Build 可提供受限的开发试用额度，存在限流、拥塞与模型下线风险；"
            "它不是长期免费生产额度，可用模型、试用资格和使用边界以 Build 控制台为准。"
        ),
        templates=(
            FreeModelTemplate("meta/llama-3.3-70b-instruct", "Llama 3.3 70B Instruct"),
        ),
    ),
)


def build_free_model_catalog_payload(
    availabilities: Iterable[ModelProfileAvailability],
) -> list[dict[str, object]]:
    """生成给 WebUI 的目录，并标记哪些免费连接已可供平台访客使用。

    只有已启用、已验证可调用、且由管理员显式标记为 ``free_quota`` 的连接才会被
    视为访客可用。这避免将“存在于免费目录但尚未配置 Key”的服务商误报为可调用。
    """

    available_by_provider: dict[str, list[ModelProfileAvailability]] = {}
    for item in availabilities:
        if (
            item.readiness is ModelReadiness.READY
            and item.profile.cost_tier is ModelCostTier.FREE_QUOTA
        ):
            available_by_provider.setdefault(item.profile.provider_key, []).append(item)

    payload: list[dict[str, object]] = []
    for provider in FREE_MODEL_PROVIDERS:
        ready_connections = available_by_provider.get(provider.provider_key, [])
        payload.append(
            {
                "provider_key": provider.provider_key,
                "display_name": provider.display_name,
                "api_base_url": provider.api_base_url,
                "website_url": provider.website_url,
                "setup_url": provider.setup_url,
                "documentation_url": provider.documentation_url,
                "pricing_url": provider.pricing_url,
                "free_label": provider.free_label,
                "free_description": provider.free_description,
                "platform_ready": bool(ready_connections),
                "visitor_ready": bool(ready_connections),
                "availability_label": (
                    "该服务商已有可用模型"
                    if ready_connections
                    else "需管理员申请并保存 API Key"
                ),
                "configured_profiles": [
                    {
                        "id": str(item.profile.id),
                        "display_name": item.profile.display_name,
                        "model_id": item.profile.model_id,
                    }
                    for item in ready_connections
                ],
                "models": [
                    {
                        "model_id": template.model_id,
                        "display_name": template.display_name,
                        "supports_vision": template.supports_vision,
                    }
                    for template in provider.templates
                ],
            },
        )
    return payload
