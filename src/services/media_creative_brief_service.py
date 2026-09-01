from __future__ import annotations

import re
import zlib
from typing import Any


class MediaCreativeBriefService:
    """把内容模型给出的宽泛描述收敛成可执行的图像与视频创作合同。

    SummaryTask 负责判断项目价值，ImageTask 和 ShortVideoPromptTask 分别负责生图与
    视频。它们如果各自临时猜测版式和镜头语言，极容易得到千篇一律的蓝色流程图或
    与口播脱节的视频片段。本服务位于三者之间，只做纯文本和结构化数据规范化：
    不访问模型、不写数据库，也不持有共享状态，因此可被多个任务安全复用。
    """

    version = "creative_brief_v2"
    _diagram_types = {
        "structural_breakdown",
        "linear_progression",
        "circular_flow",
        "hub_spoke",
        "layered_system",
        "comparison",
    }
    _palette_keys = (
        "paper_cobalt_amber",
        "paper_violet_coral",
        "paper_teal_tangerine",
        "paper_ink_lime",
        "paper_navy_orange",
    )
    _url_pattern = re.compile(r"https?://\S+", re.IGNORECASE)
    _space_pattern = re.compile(r"\s+")
    _unsafe_label_pattern = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9·+/#-]")
    _relationship_labels = {
        "强耦合",
        "弱耦合",
        "异步调用",
        "同步调用",
        "数据流",
        "事件推送",
    }
    _relationship_label_aliases = {
        "调用": "同步调用",
        "同步": "同步调用",
        "异步": "异步调用",
        "流转": "数据流",
        "传递": "数据流",
        "输入": "数据流",
        "输出": "数据流",
        "事件": "事件推送",
        "推送": "事件推送",
    }

    def normalize_visual_brief(
        self,
        raw_brief: Any,
        repository_full_name: str,
        fallback_text: str,
        project_index: int,
    ) -> dict[str, Any]:
        """生成一份稳定的单项目视觉说明书。

        输入可以是 LLM 输出的任意对象；不合法字段会被丢弃并由确定性兜底补齐。输出
        只使用短中文标签和有限节点，以便 Seedream 直接出图而不是依赖本地叠字。
        """

        source = raw_brief if isinstance(raw_brief, dict) else {}
        diagram_type = str(source.get("diagram_type", "")).strip()
        if diagram_type not in self._diagram_types:
            diagram_type = self._fallback_diagram_type(repository_full_name, project_index)

        teaching_goal = self._clean_text(
            source.get("teaching_goal"), repository_full_name, max_length=120
        ) or "用一张图说明该项目的工程机制与信息流向"
        visual_thesis = self._clean_text(
            source.get("visual_thesis"), repository_full_name, max_length=160
        ) or self._clean_text(fallback_text, repository_full_name, max_length=160)
        if not visual_thesis:
            visual_thesis = "把输入、核心处理、反馈或输出之间的关键关系讲清楚"

        nodes = self._normalize_nodes(source.get("nodes"), repository_full_name)
        if not nodes:
            nodes = self._fallback_nodes(diagram_type)
        relationships = self._normalize_relationships(source.get("relationships"), nodes)
        if not relationships:
            relationships = self._fallback_relationships(nodes, diagram_type)

        reading_order = self._normalize_reading_order(source.get("reading_order"), nodes)
        labels = self._normalize_labels(source.get("chinese_labels"), nodes)
        palette_key = str(source.get("palette_key", "")).strip()
        if palette_key not in self._palette_keys:
            palette_key = self._palette_for(repository_full_name, project_index)

        style = str(source.get("style", "")).strip() or "technical_schematic"
        negative_constraints = self._normalize_negative_constraints(source.get("negative_constraints"))

        return {
            "version": self.version,
            "teaching_goal": teaching_goal,
            "diagram_type": diagram_type,
            "visual_thesis": visual_thesis,
            "nodes": nodes,
            "relationships": relationships,
            "reading_order": reading_order,
            "chinese_labels": labels,
            "palette_key": palette_key,
            "style": style,
            "negative_constraints": negative_constraints,
        }

    def normalize_video_brief(
        self,
        raw_brief: Any,
        visual_brief: dict[str, Any],
        project_summary_text: str,
        repository_full_name: str,
        project_index: int,
    ) -> dict[str, Any]:
        """把项目叙事收敛为可衔接的短视频镜头意图。

        此合同不要求 Seedance 在画面内生成可读长文字。旁白与字幕后续由 TTS/
        HyperFrames 统一处理，模型只负责连续、可理解的动态教学画面。
        """

        source = raw_brief if isinstance(raw_brief, dict) else {}
        fallback_claim = self._clean_text(project_summary_text, repository_full_name, max_length=140)
        if not fallback_claim:
            fallback_claim = str(visual_brief.get("visual_thesis", "")).strip()
        return {
            "version": self.version,
            "narrative_claim": self._clean_text(
                source.get("narrative_claim"), repository_full_name, max_length=150
            )
            or fallback_claim
            or "该项目把一个工程环节从手工处理转成可解释的流程",
            "evidence_line": self._clean_text(
                source.get("evidence_line"), repository_full_name, max_length=120
            )
            or "用本周增长、项目定位和模块关系支撑判断，不补造外部数据",
            "mechanism": self._clean_text(
                source.get("mechanism"), repository_full_name, max_length=150
            )
            or str(visual_brief.get("visual_thesis", "")),
            "reader_gain": self._clean_text(
                source.get("reader_gain"), repository_full_name, max_length=120
            )
            or "帮助读者判断它适合放进哪一段工程工作流",
            "motion_metaphor": self._clean_text(
                source.get("motion_metaphor"), repository_full_name, max_length=100
            )
            or self._fallback_motion_metaphor(str(visual_brief.get("diagram_type", "")), project_index),
            "camera": self._clean_text(source.get("camera"), repository_full_name, max_length=80)
            or "稳定中景缓推，聚焦关键节点，不使用夸张电影运镜",
            "transition": self._clean_text(source.get("transition"), repository_full_name, max_length=100)
            or "让当前主流程线延伸到画面右侧，作为下一项目的进入线索",
            "audio_directive": self._clean_text(
                source.get("audio_directive"), repository_full_name, max_length=120
            )
            or "不生成旁白、人物口型或内嵌字幕；只保留轻微信息提示音空间",
        }

    def build_hyperframes_blueprint(
        self,
        title: str,
        scenes: list[dict[str, Any]],
        total_duration_seconds: int,
    ) -> dict[str, Any]:
        """生成供后期确定性装配使用的 HyperFrames 蓝图，不直接渲染视频。"""

        segments: list[dict[str, Any]] = []
        for scene in scenes:
            contract = scene.get("scene_contract") if isinstance(scene.get("scene_contract"), dict) else {}
            segments.append(
                {
                    "scene_index": int(scene.get("scene_index", len(segments) + 1) or len(segments) + 1),
                    "time_range": str(scene.get("time_range", "")),
                    "duration_seconds": int(scene.get("duration_seconds", 0) or 0),
                    "role": str(scene.get("purpose", "教学段")),
                    "visual_source": "seedance_dynamic_clip",
                    "narration_source": "single_tts_voiceover",
                    "caption_source": "deterministic_chinese_caption_track",
                    "transition_out": str(contract.get("exit_state", scene.get("transition_to_next", ""))),
                    "motion_contract": {
                        "entry_state": str(contract.get("entry_state", "")),
                        "beats": contract.get("beats", []),
                        "camera": str(contract.get("camera", "")),
                    },
                }
            )

        return {
            "version": "hyperframes_faceless_explainer_v1",
            "workflow": "faceless-explainer",
            "render_contract": "hyperframes_post_production_v1",
            "title": self._clean_text(title, "", max_length=80),
            "canvas": {"width": 1920, "height": 1080, "fps": 30},
            "duration_seconds": total_duration_seconds,
            "segments": segments,
            "caption_policy": "deterministic_burned_chinese_subtitles",
            "transition_policy": "shared_motif_flowline",
            "media_roles": {
                "seedance": "dynamic_segments",
                "tts": "single_voiceover",
                "article_images": "article_only_or_explicit_reference_if_enabled",
            },
            "validation_gate": [
                "npx hyperframes lint",
                "npx hyperframes check",
                "snapshot_review",
                "user_approval_before_render",
            ],
            "render_mode": "pending_user_approval",
        }

    def _normalize_nodes(self, raw_nodes: Any, repository_full_name: str) -> list[dict[str, str]]:
        if not isinstance(raw_nodes, list):
            return []
        normalized: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for index, raw_node in enumerate(raw_nodes[:4], start=1):
            if not isinstance(raw_node, dict):
                continue
            node_id = self._safe_identifier(raw_node.get("id"), fallback=f"node_{index}")
            if node_id in seen_ids:
                node_id = f"{node_id}_{index}"
            label = self._short_label(raw_node.get("label"))
            if not label:
                continue
            seen_ids.add(node_id)
            normalized.append(
                {
                    "id": node_id,
                    "label": label,
                    "role": self._clean_text(raw_node.get("role"), repository_full_name, max_length=36)
                    or "关键节点",
                }
            )
        return normalized

    def _normalize_relationships(self, raw_relationships: Any, nodes: list[dict[str, str]]) -> list[dict[str, str]]:
        if not isinstance(raw_relationships, list):
            return []
        valid_ids = {node["id"] for node in nodes}
        normalized: list[dict[str, str]] = []
        for raw_relationship in raw_relationships[:4]:
            if not isinstance(raw_relationship, dict):
                continue
            from_node = self._safe_identifier(raw_relationship.get("from"), fallback="")
            to_node = self._safe_identifier(raw_relationship.get("to"), fallback="")
            if not from_node or not to_node or from_node == to_node:
                continue
            if from_node not in valid_ids or to_node not in valid_ids:
                continue
            normalized.append(
                {
                    "from": from_node,
                    "to": to_node,
                    "label": self._relationship_label(raw_relationship.get("label")),
                }
            )
        return normalized

    def _normalize_reading_order(self, raw_order: Any, nodes: list[dict[str, str]]) -> list[str]:
        valid_ids = [node["id"] for node in nodes]
        if not isinstance(raw_order, list):
            return valid_ids
        ordered: list[str] = []
        for value in raw_order:
            node_id = self._safe_identifier(value, fallback="")
            if node_id in valid_ids and node_id not in ordered:
                ordered.append(node_id)
        return ordered + [node_id for node_id in valid_ids if node_id not in ordered]

    def _normalize_labels(self, raw_labels: Any, nodes: list[dict[str, str]]) -> list[str]:
        labels: list[str] = []
        if isinstance(raw_labels, list):
            for raw_label in raw_labels:
                label = self._short_label(raw_label)
                if label and label not in labels:
                    labels.append(label)
                if len(labels) >= 4:
                    break
        for node in nodes:
            label = node["label"]
            if label not in labels:
                labels.append(label)
            if len(labels) >= 4:
                break
        return labels[:4]

    def _normalize_negative_constraints(self, raw_constraints: Any) -> list[str]:
        defaults = [
            "不要大段英文或代码",
            "不要仓库地址、网址、logo或水印",
            "不要密集小字、伪文字或乱码",
            "不要抽象海报、霓虹满屏或杂乱网线",
        ]
        if not isinstance(raw_constraints, list):
            return defaults
        normalized: list[str] = []
        for raw_constraint in raw_constraints:
            constraint = self._clean_text(raw_constraint, "", max_length=60)
            if constraint and constraint not in normalized:
                normalized.append(constraint)
            if len(normalized) >= 6:
                break
        return normalized or defaults

    def _fallback_nodes(self, diagram_type: str) -> list[dict[str, str]]:
        templates = {
            "circular_flow": [("task", "任务", "触发"), ("plan", "计划", "拆解"), ("run", "执行", "处理"), ("feedback", "反馈", "校正")],
            "hub_spoke": [("core", "核心", "中枢"), ("input", "输入", "来源"), ("tools", "工具", "协作"), ("output", "输出", "结果")],
            "layered_system": [("top", "体验层", "呈现"), ("middle", "能力层", "编排"), ("bottom", "资源层", "支撑")],
            "comparison": [("before", "旧流程", "对照"), ("change", "改造点", "变化"), ("after", "新流程", "结果")],
            "linear_progression": [("input", "输入", "起点"), ("process", "处理", "核心"), ("verify", "校验", "保障"), ("output", "输出", "结果")],
            "structural_breakdown": [("context", "上下文", "输入"), ("core", "核心层", "处理"), ("tools", "工具层", "执行"), ("result", "结果", "输出")],
        }
        return [
            {"id": node_id, "label": label, "role": role}
            for node_id, label, role in templates.get(diagram_type, templates["structural_breakdown"])
        ]

    def _fallback_relationships(self, nodes: list[dict[str, str]], diagram_type: str) -> list[dict[str, str]]:
        if len(nodes) < 2:
            return []
        relationships = [
            {"from": nodes[index]["id"], "to": nodes[index + 1]["id"], "label": "流转"}
            for index in range(len(nodes) - 1)
        ]
        if diagram_type == "circular_flow" and len(nodes) > 2:
            relationships.append({"from": nodes[-1]["id"], "to": nodes[0]["id"], "label": "反馈"})
        return relationships[:4]

    def _fallback_diagram_type(self, repository_full_name: str, project_index: int) -> str:
        name = repository_full_name.lower()
        if any(token in name for token in ("agent", "loop", "workflow")):
            return "circular_flow"
        if any(token in name for token in ("graph", "map", "knowledge")):
            return "hub_spoke"
        if any(token in name for token in ("skill", "plugin", "tool")):
            return "layered_system"
        return ("structural_breakdown", "linear_progression", "comparison")[project_index % 3]

    def _palette_for(self, repository_full_name: str, project_index: int) -> str:
        stable_number = zlib.crc32(repository_full_name.encode("utf-8")) + max(project_index, 0)
        return self._palette_keys[stable_number % len(self._palette_keys)]

    def _fallback_motion_metaphor(self, diagram_type: str, project_index: int) -> str:
        motions = {
            "circular_flow": "核心节点亮起，信息沿环形路径回流，最后落在反馈节点",
            "hub_spoke": "中心节点展开到周边能力点，再收回一条主路径",
            "layered_system": "三层模块从下向上搭建，层间连线逐级点亮",
            "comparison": "旧流程淡出，改造节点被替换，新流程稳定落位",
            "linear_progression": "一条主流程线从左向右推进，关键步骤依次高亮",
            "structural_breakdown": "中心模块展开为几个有序部件，再回收为完整机制",
        }
        return motions.get(diagram_type, motions["structural_breakdown"])

    def _clean_text(self, value: Any, repository_full_name: str, max_length: int) -> str:
        text = str(value or "")
        text = self._url_pattern.sub("", text)
        if repository_full_name:
            text = text.replace(repository_full_name, "该项目")
            for part in repository_full_name.split("/", 1):
                if len(part) >= 3:
                    text = text.replace(part, "")
        text = text.replace("`", "").replace("#", "").replace("*", "")
        text = self._space_pattern.sub(" ", text).strip(" ，,。；;：:|-")
        return text[:max_length].rstrip(" ，,。；;：:")

    def _short_label(self, value: Any) -> str:
        label = self._clean_text(value, "", max_length=12)
        label = self._unsafe_label_pattern.sub("", label)
        return label

    def _relationship_label(self, value: Any) -> str:
        """把连线文字限制为教学图约定的六类耦合或数据关系。"""

        label = self._short_label(value)
        if label in self._relationship_labels:
            return label
        return self._relationship_label_aliases.get(label, "数据流")

    @staticmethod
    def _safe_identifier(value: Any, fallback: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_-]", "", str(value or "").strip().lower())
        return normalized[:24] or fallback
