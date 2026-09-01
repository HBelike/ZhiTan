from __future__ import annotations

import json
import re
from typing import Any, Iterable

from src.services.article_visual_spec import (
    ARTICLE_VISUAL_SPEC_VERSION,
    FIGURE_ROLES,
    ArticleVisualSpecError,
    ValidatedArticleVisualSpec,
)


class ArticleVisualSpecValidator:
    """把模型输出收敛成证据可追踪且适配固定模板的视觉规格。"""

    _common_fields = frozenset(
        {
            "version",
            "repository_full_name",
            "figure_role",
            "purpose",
            "headline",
            "evidence_refs",
            "takeaways",
            "art_direction",
        }
    )
    _role_fields = {
        "summary_card": frozenset({"positioning", "capabilities"}),
        "flow": frozenset({"steps", "edges"}),
        "architecture": frozenset({"nodes", "edges", "layers"}),
        "comparison": frozenset({"left", "right", "dimensions"}),
        "timeline": frozenset({"events"}),
    }
    _forbidden_meta_words = (
        "16:9",
        "16.9",
        "工程架构",
        "架构信息图",
        "技术信息图",
        "流程图",
        "对比图",
        "示意图",
        "无标题",
    )
    _aspect_ratio_pattern = re.compile(r"16\s*[:：./]\s*9", re.IGNORECASE)
    _placeholder_pattern = re.compile(
        r"(?:^|\b)(?:todo|tbd|placeholder|lorem\s+ipsum|x{3,})(?:$|\b)|"
        r"待补充|待填写|请输入|占位(?:符|内容|文字)?",
        re.IGNORECASE,
    )
    _identifier_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")
    _evidence_kinds = frozenset(
        {
            "repository_file",
            "repository_source",
            "repository_metadata",
            "weekly_ranking",
            "generated_article",
            "article_claim",
        }
    )
    _styles = frozenset({"notion"})
    _palettes = frozenset({"editorial_blue"})
    _densities = frozenset({"low", "medium", "high"})
    _max_total_visible_chars = 1200

    def validate(
        self,
        raw_spec: dict[str, Any],
        repository_full_name: str,
        allowed_evidence_paths: set[str] | None = None,
    ) -> ValidatedArticleVisualSpec:
        if not isinstance(raw_spec, dict):
            raise ArticleVisualSpecError("visual_spec 必须是对象")
        expected_repository = self._text(
            repository_full_name,
            "repository_full_name",
            max_length=160,
        )
        role = self._text(raw_spec.get("figure_role"), "figure_role", max_length=32)
        if role not in FIGURE_ROLES:
            raise ArticleVisualSpecError(f"figure_role 不受支持：{role}")
        self._reject_unknown_fields(
            raw_spec,
            self._common_fields | self._role_fields[role],
            "visual_spec",
        )

        version = self._text(raw_spec.get("version"), "version", max_length=64)
        if version != ARTICLE_VISUAL_SPEC_VERSION:
            raise ArticleVisualSpecError(
                f"version 必须为 {ARTICLE_VISUAL_SPEC_VERSION}"
            )
        actual_repository = self._text(
            raw_spec.get("repository_full_name"),
            "repository_full_name",
            max_length=160,
        )
        if actual_repository != expected_repository:
            raise ArticleVisualSpecError(
                "repository_full_name 与当前项目不一致："
                f"expected={expected_repository} actual={actual_repository}"
            )

        purpose = self._text(raw_spec.get("purpose"), "purpose", max_length=80)
        headline = self._text(raw_spec.get("headline"), "headline", max_length=30)
        evidence_refs = self._validate_evidence_refs(
            raw_spec.get("evidence_refs"), allowed_evidence_paths
        )
        takeaways = self._text_list(
            raw_spec.get("takeaways"),
            "takeaways",
            minimum=1,
            maximum=3,
            item_max_length=50,
        )
        art_direction = self._validate_art_direction(raw_spec.get("art_direction"))

        common: dict[str, Any] = {
            "version": version,
            "repository_full_name": actual_repository,
            "figure_role": role,
            "purpose": purpose,
            "headline": headline,
            "evidence_refs": evidence_refs,
            "takeaways": takeaways,
            "art_direction": art_direction,
        }
        role_value, role_texts, node_ids, edges = self._validate_role(role, raw_spec)
        value = {**common, **role_value}
        visible_texts = (purpose, headline, *takeaways, *role_texts)
        self._validate_visible_texts(visible_texts)
        canonical_json = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return ValidatedArticleVisualSpec(
            value=value,
            canonical_json=canonical_json,
            repository_full_name=actual_repository,
            figure_role=role,
            visible_texts=tuple(visible_texts),
            node_ids=tuple(node_ids),
            edges=tuple(edges),
        )

    def _validate_role(
        self,
        role: str,
        raw_spec: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str], list[str], list[tuple[str, str]]]:
        if role == "summary_card":
            return self._validate_summary_card(raw_spec)
        if role == "flow":
            return self._validate_flow(raw_spec)
        if role == "architecture":
            return self._validate_architecture(raw_spec)
        if role == "comparison":
            return self._validate_comparison(raw_spec)
        return self._validate_timeline(raw_spec)

    def _validate_summary_card(
        self, raw_spec: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], list[tuple[str, str]]]:
        positioning = self._text(
            raw_spec.get("positioning"), "positioning", max_length=180
        )
        capabilities = self._object_list(
            raw_spec.get("capabilities"), "capabilities", 2, 4
        )
        normalized: list[dict[str, str]] = []
        visible = [positioning]
        labels: set[str] = set()
        for index, item in enumerate(capabilities):
            path = f"capabilities[{index}]"
            self._reject_unknown_fields(item, {"label", "description"}, path)
            label = self._text(item.get("label"), f"{path}.label", max_length=16)
            description = self._text(
                item.get("description"), f"{path}.description", max_length=150
            )
            if label in labels:
                raise ArticleVisualSpecError(f"capabilities.label 重复：{label}")
            labels.add(label)
            normalized.append({"label": label, "description": description})
            visible.extend((label, description))
        return {"positioning": positioning, "capabilities": normalized}, visible, [], []

    def _validate_flow(
        self, raw_spec: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], list[tuple[str, str]]]:
        steps, visible, node_ids = self._validate_nodes(
            raw_spec.get("steps"), "steps", 1, None, allow_layer=False
        )
        edges, edge_pairs, edge_texts = self._validate_edges(
            raw_spec.get("edges"),
            node_ids,
            "edges",
            minimum=0,
            maximum=None,
        )
        expected_pairs = list(zip(node_ids, node_ids[1:]))
        if edge_pairs != expected_pairs:
            raise ArticleVisualSpecError(
                "flow.edges 必须恰好构成 steps 的相邻顺序链："
                f"expected={expected_pairs} actual={edge_pairs}"
            )
        return {"steps": steps, "edges": edges}, visible + edge_texts, node_ids, edge_pairs

    def _validate_architecture(
        self, raw_spec: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], list[tuple[str, str]]]:
        layers, layer_texts, layer_ids = self._validate_layers(raw_spec.get("layers"))
        nodes, visible, node_ids = self._validate_nodes(
            raw_spec.get("nodes"), "nodes", 3, 7, allow_layer=bool(layers)
        )
        if layers:
            for index, node in enumerate(nodes):
                layer = node.get("layer")
                if layer is None:
                    raise ArticleVisualSpecError(
                        f"nodes[{index}] 必须显式引用 layer"
                    )
                if layer not in layer_ids:
                    raise ArticleVisualSpecError(
                        f"nodes[{index}].layer 引用了无效层：{layer}"
                    )
            used_layer_ids = {node["layer"] for node in nodes}
            unused_layer_ids = sorted(layer_ids - used_layer_ids)
            if unused_layer_ids:
                raise ArticleVisualSpecError(
                    "architecture.layers 中有层没有节点使用："
                    f"{', '.join(unused_layer_ids)}"
                )
        edges, edge_pairs, edge_texts = self._validate_edges(
            raw_spec.get("edges"), node_ids, "edges", minimum=2, maximum=12
        )
        value: dict[str, Any] = {"nodes": nodes, "edges": edges}
        if layers:
            value["layers"] = layers
        return value, layer_texts + visible + edge_texts, node_ids, edge_pairs

    def _validate_comparison(
        self, raw_spec: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], list[tuple[str, str]]]:
        left, left_texts = self._validate_comparison_side(raw_spec.get("left"), "left")
        right, right_texts = self._validate_comparison_side(raw_spec.get("right"), "right")
        dimensions = self._object_list(
            raw_spec.get("dimensions"), "dimensions", 2, 4
        )
        normalized: list[dict[str, str]] = []
        visible = left_texts + right_texts
        labels: set[str] = set()
        for index, item in enumerate(dimensions):
            path = f"dimensions[{index}]"
            self._reject_unknown_fields(item, {"label", "left", "right"}, path)
            dimension = {
                "label": self._text(item.get("label"), f"{path}.label", max_length=16),
                "left": self._text(item.get("left"), f"{path}.left", max_length=40),
                "right": self._text(item.get("right"), f"{path}.right", max_length=40),
            }
            if dimension["label"] in labels:
                raise ArticleVisualSpecError(
                    f"dimensions.label 重复：{dimension['label']}"
                )
            labels.add(dimension["label"])
            normalized.append(dimension)
            visible.extend(dimension.values())
        return {
            "left": left,
            "right": right,
            "dimensions": normalized,
        }, visible, [], []

    def _validate_timeline(
        self, raw_spec: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], list[tuple[str, str]]]:
        events = self._object_list(raw_spec.get("events"), "events", 3, 6)
        normalized: list[dict[str, str]] = []
        visible: list[str] = []
        event_ids: list[str] = []
        for index, item in enumerate(events):
            path = f"events[{index}]"
            self._reject_unknown_fields(
                item, {"id", "time", "label", "description"}, path
            )
            event_id = self._identifier(item.get("id"), f"{path}.id")
            if event_id in event_ids:
                raise ArticleVisualSpecError(f"节点 ID 重复：{event_id}")
            event = {
                "id": event_id,
                "time": self._text(item.get("time"), f"{path}.time", max_length=16),
                "label": self._text(item.get("label"), f"{path}.label", max_length=18),
                "description": self._text(
                    item.get("description"), f"{path}.description", max_length=60
                ),
            }
            event_ids.append(event_id)
            normalized.append(event)
            visible.extend((event["time"], event["label"], event["description"]))
        return {"events": normalized}, visible, event_ids, []

    def _validate_nodes(
        self,
        raw_nodes: Any,
        field: str,
        minimum: int,
        maximum: int | None,
        *,
        allow_layer: bool,
    ) -> tuple[list[dict[str, str]], list[str], list[str]]:
        nodes = self._object_list(raw_nodes, field, minimum, maximum)
        normalized: list[dict[str, str]] = []
        visible: list[str] = []
        node_ids: list[str] = []
        node_labels: set[str] = set()
        allowed = {"id", "label", "description"}
        if allow_layer:
            allowed.add("layer")
        for index, item in enumerate(nodes):
            path = f"{field}[{index}]"
            self._reject_unknown_fields(item, allowed, path)
            node_id = self._identifier(item.get("id"), f"{path}.id")
            if node_id in node_ids:
                raise ArticleVisualSpecError(f"节点 ID 重复：{node_id}")
            node = {
                "id": node_id,
                "label": self._text(item.get("label"), f"{path}.label", max_length=18),
                "description": self._text(
                    item.get("description"), f"{path}.description", max_length=60
                ),
            }
            if node["label"] in node_labels:
                raise ArticleVisualSpecError(f"节点 label 重复：{node['label']}")
            if allow_layer and "layer" in item:
                node["layer"] = self._identifier(item.get("layer"), f"{path}.layer")
            node_ids.append(node_id)
            node_labels.add(node["label"])
            normalized.append(node)
            visible.extend((node["label"], node["description"]))
        return normalized, visible, node_ids

    def _validate_edges(
        self,
        raw_edges: Any,
        node_ids: list[str],
        field: str,
        *,
        minimum: int,
        maximum: int | None,
    ) -> tuple[list[dict[str, str]], list[tuple[str, str]], list[str]]:
        edges = self._object_list(raw_edges, field, minimum, maximum)
        valid_ids = set(node_ids)
        normalized: list[dict[str, str]] = []
        pairs: list[tuple[str, str]] = []
        visible: list[str] = []
        for index, item in enumerate(edges):
            path = f"{field}[{index}]"
            self._reject_unknown_fields(item, {"from", "to", "label"}, path)
            source = self._identifier(item.get("from"), f"{path}.from")
            target = self._identifier(item.get("to"), f"{path}.to")
            if source not in valid_ids or target not in valid_ids:
                raise ArticleVisualSpecError(
                    f"{path} 引用了无效节点：{source}->{target}"
                )
            if source == target:
                raise ArticleVisualSpecError(f"{path} 不允许自环：{source}")
            pair = (source, target)
            if pair in pairs:
                raise ArticleVisualSpecError(f"{path} 存在重复边：{source}->{target}")
            edge = {"from": source, "to": target}
            if "label" in item:
                label = self._text(item.get("label"), f"{path}.label", max_length=16)
                edge["label"] = label
                visible.append(label)
            pairs.append(pair)
            normalized.append(edge)
        return normalized, pairs, visible

    def _validate_layers(
        self, raw_layers: Any
    ) -> tuple[list[dict[str, str]], list[str], set[str]]:
        if raw_layers is None:
            return [], [], set()
        layers = self._object_list(raw_layers, "layers", 1, None)
        normalized: list[dict[str, str]] = []
        visible: list[str] = []
        layer_ids: set[str] = set()
        for index, item in enumerate(layers):
            path = f"layers[{index}]"
            self._reject_unknown_fields(item, {"id", "label"}, path)
            layer_id = self._identifier(item.get("id"), f"{path}.id")
            if layer_id in layer_ids:
                raise ArticleVisualSpecError(f"层 ID 重复：{layer_id}")
            label = self._text(item.get("label"), f"{path}.label", max_length=16)
            layer_ids.add(layer_id)
            normalized.append({"id": layer_id, "label": label})
            visible.append(label)
        return normalized, visible, layer_ids

    def _validate_comparison_side(
        self, raw_side: Any, field: str
    ) -> tuple[dict[str, str], list[str]]:
        if not isinstance(raw_side, dict):
            raise ArticleVisualSpecError(f"{field} 必须是对象")
        self._reject_unknown_fields(raw_side, {"label", "description"}, field)
        side = {
            "label": self._text(raw_side.get("label"), f"{field}.label", max_length=18),
            "description": self._text(
                raw_side.get("description"), f"{field}.description", max_length=80
            ),
        }
        return side, [side["label"], side["description"]]

    def _validate_evidence_refs(
        self,
        raw_refs: Any,
        allowed_evidence_paths: set[str] | None,
    ) -> list[dict[str, str]]:
        refs = self._object_list(raw_refs, "evidence_refs", 1, 8)
        normalized: list[dict[str, str]] = []
        for index, item in enumerate(refs):
            path = f"evidence_refs[{index}]"
            self._reject_unknown_fields(item, {"kind", "path", "claim"}, path)
            kind = self._text(item.get("kind"), f"{path}.kind", max_length=40)
            if kind not in self._evidence_kinds:
                raise ArticleVisualSpecError(f"{path}.kind 不受支持：{kind}")
            evidence_path = self._text(
                item.get("path"), f"{path}.path", max_length=240
            )
            if allowed_evidence_paths and evidence_path not in allowed_evidence_paths:
                raise ArticleVisualSpecError(f"证据路径不在允许范围内：{evidence_path}")
            claim = self._text(item.get("claim"), f"{path}.claim", max_length=240)
            normalized.append({"kind": kind, "path": evidence_path, "claim": claim})
        return normalized

    def _validate_art_direction(self, raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            raise ArticleVisualSpecError("art_direction 必须是对象")
        self._reject_unknown_fields(raw, {"style", "palette", "density"}, "art_direction")
        style = self._text(raw.get("style"), "art_direction.style", max_length=32)
        palette = self._text(raw.get("palette"), "art_direction.palette", max_length=32)
        density = self._text(raw.get("density"), "art_direction.density", max_length=16)
        if style not in self._styles:
            raise ArticleVisualSpecError(f"art_direction.style 不受支持：{style}")
        if palette not in self._palettes:
            raise ArticleVisualSpecError(f"art_direction.palette 不受支持：{palette}")
        if density not in self._densities:
            raise ArticleVisualSpecError(f"art_direction.density 不受支持：{density}")
        return {"style": style, "palette": palette, "density": density}

    def _validate_visible_texts(self, values: Iterable[str]) -> None:
        total = 0
        for value in values:
            total += len(value)
            if self._aspect_ratio_pattern.search(value):
                raise ArticleVisualSpecError(f"可见文字包含元文字“16:9”：{value}")
            for phrase in self._forbidden_meta_words:
                if phrase.casefold() in value.casefold():
                    raise ArticleVisualSpecError(f"可见文字包含元文字“{phrase}”：{value}")
            if self._placeholder_pattern.search(value):
                raise ArticleVisualSpecError(f"可见文字包含占位内容：{value}")
        if total > self._max_total_visible_chars:
            raise ArticleVisualSpecError(
                f"可见文字总量超过模板容量：{total}>{self._max_total_visible_chars}"
            )

    def _object_list(
        self,
        raw: Any,
        field: str,
        minimum: int,
        maximum: int | None,
    ) -> list[dict[str, Any]]:
        valid_count = isinstance(raw, list) and len(raw) >= minimum
        if valid_count and maximum is not None:
            valid_count = len(raw) <= maximum
        if not valid_count:
            expected = (
                f"至少为 {minimum}"
                if maximum is None
                else f"为 {minimum}–{maximum}"
            )
            raise ArticleVisualSpecError(
                f"{field} 数量必须{expected}，当前为 "
                f"{len(raw) if isinstance(raw, list) else '非列表'}"
            )
        if any(not isinstance(item, dict) for item in raw):
            raise ArticleVisualSpecError(f"{field} 的每一项都必须是对象")
        return raw

    def _text_list(
        self,
        raw: Any,
        field: str,
        *,
        minimum: int,
        maximum: int,
        item_max_length: int,
    ) -> list[str]:
        if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
            raise ArticleVisualSpecError(f"{field} 数量必须为 {minimum}–{maximum}")
        return [
            self._text(value, f"{field}[{index}]", max_length=item_max_length)
            for index, value in enumerate(raw)
        ]

    def _identifier(self, raw: Any, field: str) -> str:
        value = self._text(raw, field, max_length=48)
        if not self._identifier_pattern.fullmatch(value):
            raise ArticleVisualSpecError(
                f"{field} 必须是以英文字母开头的稳定标识符：{value}"
            )
        return value

    @staticmethod
    def _reject_unknown_fields(
        raw: dict[str, Any], allowed: set[str] | frozenset[str], path: str
    ) -> None:
        unknown = sorted(set(raw) - set(allowed))
        if unknown:
            raise ArticleVisualSpecError(f"{path} 包含未知字段：{', '.join(unknown)}")

    @staticmethod
    def _text(raw: Any, field: str, *, max_length: int) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise ArticleVisualSpecError(f"{field} 必须是非空文字")
        value = raw.strip()
        if len(value) > max_length:
            raise ArticleVisualSpecError(
                f"{field} 超过模板容量：{len(value)}>{max_length}；禁止静默截断"
            )
        return value


__all__ = ["ArticleVisualSpecError", "ArticleVisualSpecValidator"]
