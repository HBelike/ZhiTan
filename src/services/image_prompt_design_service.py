from __future__ import annotations

import re
from typing import Any

from src.config.config_manager import AppConfig
from src.services.media_creative_brief_service import MediaCreativeBriefService


class ImagePromptDesignService:
    """把内容简报编译成可直接交给 Seedream 的中文技术教学图提示词。

    SummaryTask 只负责给出项目价值与 ``visual_brief``；本服务不猜测某个项目应当
    使用通用蓝色流程图，而是根据 diagram_type、真实节点和关系生成一张独立的
    无标题工程架构信息图。该服务不调用外部 API、不写数据库，也不做本地叠字，最终图像
    始终由火山方舟原始生成。
    """

    _whitespace_pattern = re.compile(r"\s+")
    _url_pattern = re.compile(r"https?://\S+", re.IGNORECASE)
    _repo_pattern = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._brief_service = MediaCreativeBriefService()

    def build_project_architecture_prompt(
        self,
        repository_full_name: str,
        focus_prompt: str,
        project_summary_text: str = "",
        visual_brief: dict[str, Any] | None = None,
        project_index: int = 1,
    ) -> str:
        """生成一段可直接提交给 Ark Seedream 的最终架构图提示词。

        输入：项目名仅用于清理文本和确定性兜底；``visual_brief`` 给出图表类型、节点、
        关系，``focus_prompt``/``project_summary_text`` 用于补充工程语义。

        输出：长度受 ``image.prompt.max_length`` 控制的中文提示词。

        失败处理：不访问网络；不完整的简报由 ``MediaCreativeBriefService`` 补全。
        线程安全：不持有跨请求可变状态。
        """

        cleaned_focus = self._sanitize_visual_text(focus_prompt, repository_full_name, max_length=260)
        cleaned_summary = self._sanitize_visual_text(project_summary_text, repository_full_name, max_length=260)
        brief = self._brief_service.normalize_visual_brief(
            raw_brief=visual_brief,
            repository_full_name=repository_full_name,
            fallback_text=cleaned_focus or cleaned_summary,
            project_index=project_index,
        )

        nodes = brief.get("nodes") if isinstance(brief.get("nodes"), list) else []
        relationships = (
            brief.get("relationships") if isinstance(brief.get("relationships"), list) else []
        )
        diagram_type = str(brief.get("diagram_type", "structural_breakdown"))
        layout_instruction = self._format_layout(
            diagram_type=diagram_type,
            raw_nodes=nodes,
            raw_relationships=relationships,
            raw_reading_order=brief.get("reading_order"),
        )
        relationship_instruction = self._format_relationships(relationships, nodes)

        # 图片模型最容易在抽象的“中心连接周边”指令中自行补齐对称节点。最终 Prompt
        # 因此只保留可以直接照着画的节点数量、位置和箭头拓扑，不再重复项目摘要或要求
        # 模型排版连线文字。详细语义仍由同源 visual_brief 和文章图注承担。
        style_contract = self._compact_text(self.config.image_prompt_visual_system, max_length=60)
        body_parts = [
            style_contract,
            (
                f"仅绘制{len(nodes)}个模块，每个模块只出现一次；"
                "禁止镜像、复制或新增，禁止为对称构图补节点。"
            ),
            layout_instruction,
            f"深灰正交箭头仅限：{relationship_instruction}。",
        ]
        terminal_contract = (
            "画面只写节点标签，连线不写文字；禁止标题区、装饰点、空白占位框、"
            "曲线、交叉线、人物、logo、水印、伪文字和乱码。"
        )
        prompt = " ".join(part for part in body_parts if part)
        available = max(1, self.config.image_prompt_max_length - len(terminal_contract) - 1)
        prompt = self._limit_text(prompt, max_length=available)
        return f"{prompt} {terminal_contract}".strip()

    def _format_layout(
        self,
        diagram_type: str,
        raw_nodes: Any,
        raw_relationships: Any,
        raw_reading_order: Any,
    ) -> str:
        """把抽象图表类型编译成每个节点唯一且可执行的位置指令。"""

        if not isinstance(raw_nodes, list):
            return "位置：模块沿单一主线规整排列。"
        label_map = {
            str(node.get("id", "")).strip(): self._short_text(node.get("label"), 8)
            for node in raw_nodes
            if isinstance(node, dict) and str(node.get("id", "")).strip()
        }
        ordered_ids: list[str] = []
        if isinstance(raw_reading_order, list):
            for raw_id in raw_reading_order:
                node_id = str(raw_id).strip()
                if node_id in label_map and node_id not in ordered_ids:
                    ordered_ids.append(node_id)
        ordered_ids.extend(node_id for node_id in label_map if node_id not in ordered_ids)
        ordered_labels = [f"「{label_map[node_id]}」" for node_id in ordered_ids]
        sequence = "→".join(ordered_labels)

        if diagram_type == "hub_spoke":
            return self._format_hub_layout(label_map, ordered_ids, raw_relationships)
        if diagram_type == "circular_flow":
            return f"位置与阅读顺序：从顶部顺时针{sequence}。"
        if diagram_type == "layered_system":
            return f"位置与阅读顺序：自上而下{sequence}。"
        if diagram_type == "comparison":
            return f"位置与阅读顺序：左到右对照{sequence}。"
        if diagram_type == "structural_breakdown":
            return f"位置与阅读顺序：左到右分区{sequence}。"
        return f"位置与阅读顺序：左到右{sequence}。"

    def _format_hub_layout(
        self,
        label_map: dict[str, str],
        ordered_ids: list[str],
        raw_relationships: Any,
    ) -> str:
        """根据实际入边和出边安排 hub-spoke，避免模型做无依据的左右镜像。"""

        relationships = raw_relationships if isinstance(raw_relationships, list) else []
        degree = {node_id: 0 for node_id in ordered_ids}
        for relation in relationships:
            if not isinstance(relation, dict):
                continue
            source = str(relation.get("from", "")).strip()
            target = str(relation.get("to", "")).strip()
            if source in degree and target in degree:
                degree[source] += 1
                degree[target] += 1
        hub_id = max(ordered_ids, key=lambda node_id: degree[node_id], default="")
        if not hub_id:
            return "位置：模块沿单一主线规整排列。"

        incoming: list[str] = []
        outgoing: list[str] = []
        for relation in relationships:
            if not isinstance(relation, dict):
                continue
            source = str(relation.get("from", "")).strip()
            target = str(relation.get("to", "")).strip()
            if target == hub_id and source in label_map and source not in incoming:
                incoming.append(source)
            if source == hub_id and target in label_map and target not in outgoing:
                outgoing.append(target)
        remaining = [
            node_id
            for node_id in ordered_ids
            if node_id != hub_id and node_id not in incoming and node_id not in outgoing
        ]
        incoming.extend(remaining)

        placements = [f"中心「{label_map[hub_id]}」"]
        if len(incoming) == 1:
            placements.append(f"左侧「{label_map[incoming[0]]}」")
        elif incoming:
            left_slots = ("左上", "左下", "左侧")
            placements.extend(
                f"{slot}「{label_map[node_id]}」"
                for slot, node_id in zip(left_slots, incoming, strict=False)
            )
        if len(outgoing) == 1:
            placements.append(f"右侧「{label_map[outgoing[0]]}」")
        elif outgoing:
            right_slots = ("右上", "右下", "右侧")
            placements.extend(
                f"{slot}「{label_map[node_id]}」"
                for slot, node_id in zip(right_slots, outgoing, strict=False)
            )
        return f"位置：{'；'.join(placements)}。"

    def _format_relationships(self, raw_relationships: Any, raw_nodes: Any) -> str:
        if not isinstance(raw_relationships, list) or not isinstance(raw_nodes, list):
            return "只保留主链路与必要反馈箭头，避免复杂蜘蛛网连线。"
        label_map = {
            str(node.get("id", "")).strip(): self._short_text(node.get("label"), 8)
            for node in raw_nodes
            if isinstance(node, dict)
        }
        relationships: list[str] = []
        for raw_relation in raw_relationships[:4]:
            if not isinstance(raw_relation, dict):
                continue
            source = label_map.get(str(raw_relation.get("from", "")).strip(), "")
            target = label_map.get(str(raw_relation.get("to", "")).strip(), "")
            if source and target:
                relationships.append(f"「{source}」→「{target}」")
        return "；".join(relationships) or "主数据流"

    def _sanitize_visual_text(self, text: str, repository_full_name: str, max_length: int) -> str:
        """清理不适合进入图像提示词的 URL、仓库名和格式噪声。"""

        normalized = text or ""
        normalized = self._url_pattern.sub("", normalized)
        normalized = normalized.replace(repository_full_name, "该项目")
        for part in repository_full_name.split("/", 1):
            if len(part) >= 3:
                normalized = normalized.replace(part, "")
        normalized = self._repo_pattern.sub("该项目", normalized)
        normalized = normalized.replace("`", "").replace("*", "").replace("#", "")
        normalized = normalized.replace("<", "").replace(">", "")
        return self._compact_text(normalized, max_length=max_length).strip(" ，,。；;：:")

    def _short_text(self, value: Any, max_length: int) -> str:
        return self._compact_text(str(value or ""), max_length=max_length).strip(" ，,。；;：:")

    def _compact_text(self, text: str, max_length: int) -> str:
        compacted = self._whitespace_pattern.sub(" ", text.replace("\r", " ").replace("\n", " ")).strip()
        return self._limit_text(compacted, max_length=max_length)

    def _limit_text(self, text: str, max_length: int) -> str:
        """按配置长度截断提示词，避免运行时附加规则稀释核心约束。"""

        normalized = text.strip()
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max(1, max_length - 1)].rstrip(" ，,；;。") + "。"
