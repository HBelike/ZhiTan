from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass
from typing import Any, Callable

from src.services.article_visual_spec import ValidatedArticleVisualSpec


@dataclass(frozen=True)
class HtmlVisualDocument:
    """可直接交给 Chromium 截图的单文件视觉文档。"""

    html: str
    expected_texts: tuple[tuple[str, str], ...]
    node_ids: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]


class ArticleVisualTemplateService:
    """把已校验规格编译为五种固定、不可注入自由样式的 HTML 模板。"""

    CANVAS_WIDTH = 2048
    CANVAS_HEIGHT = 1152
    TEMPLATE_VERSION = "article_visual_v1"
    FONT_FILE_NAME = "NotoSansSC-VF.ttf"

    _body_width = 1856
    _body_height = 650

    def render(self, spec: ValidatedArticleVisualSpec) -> HtmlVisualDocument:
        expected: list[tuple[str, str]] = []
        renderers: dict[
            str,
            Callable[[dict[str, Any], list[tuple[str, str]]], str],
        ] = {
            "summary_card": self._render_summary_card,
            "flow": self._render_flow,
            "architecture": self._render_architecture,
            "comparison": self._render_comparison,
            "timeline": self._render_timeline,
        }
        try:
            body = renderers[spec.figure_role](spec.value, expected)
        except KeyError as exc:
            raise ValueError(f"没有注册视觉模板：{spec.figure_role}") from exc

        header = self._render_header(spec.value, expected)
        takeaways = self._render_takeaways(spec.value["takeaways"], expected)
        document = self._build_document(header, body, takeaways, expected)
        return HtmlVisualDocument(
            html=document,
            expected_texts=tuple(expected),
            node_ids=spec.node_ids,
            edges=spec.edges,
        )

    def _render_header(
        self,
        value: dict[str, Any],
        expected: list[tuple[str, str]],
    ) -> str:
        headline = self._text("h1", "headline", value["headline"], expected)
        purpose = self._text("p", "purpose", value["purpose"], expected)
        return (
            '<header class="visual-header" data-layout-box="header">'
            f"{headline}{purpose}"
            "</header>"
        )

    def _render_takeaways(
        self,
        values: list[str],
        expected: list[tuple[str, str]],
    ) -> str:
        cards = []
        for index, value in enumerate(values):
            text = self._text(
                "span", f"takeaway-{index}", value, expected, class_name="takeaway-text"
            )
            cards.append(
                '<div class="takeaway" '
                f'data-layout-box="takeaway-{index}"><i aria-hidden="true"></i>{text}</div>'
            )
        return '<footer class="takeaways">' + "".join(cards) + "</footer>"

    def _render_summary_card(
        self,
        value: dict[str, Any],
        expected: list[tuple[str, str]],
    ) -> str:
        positioning = self._text(
            "p",
            "summary-positioning",
            value["positioning"],
            expected,
            class_name="positioning-text",
        )
        capabilities = []
        max_description_length = max(
            len(str(item["description"])) for item in value["capabilities"]
        )
        if max_description_length > 80:
            compact_copy_class = " capability-copy-dense"
        elif max_description_length > 60:
            compact_copy_class = " capability-copy-compact"
        else:
            compact_copy_class = ""
        for index, capability in enumerate(value["capabilities"]):
            label = self._text(
                "h2", f"capability-{index}-label", capability["label"], expected
            )
            description = self._text(
                "p",
                f"capability-{index}-description",
                capability["description"],
                expected,
            )
            capabilities.append(
                '<article class="capability-card" '
                f'data-layout-box="capability-{index}">{label}{description}</article>'
            )
        return (
            '<main class="visual-body summary-layout">'
            '<section class="positioning-card" data-layout-box="positioning">'
            '<span class="section-accent" aria-hidden="true"></span>'
            f"{positioning}</section>"
            '<section class="capability-grid '
            f'capability-count-{len(capabilities)}{compact_copy_class}">'
            f'{"".join(capabilities)}</section></main>'
        )

    def _render_flow(
        self,
        value: dict[str, Any],
        expected: list[tuple[str, str]],
    ) -> str:
        steps = value["steps"]
        count = len(steps)
        if count <= 5:
            columns = count
            rows = 1
            horizontal_gap = 72
            vertical_gap = 0
            card_width = min(
                360,
                (self._body_width - horizontal_gap * (columns - 1)) // columns,
            )
            card_height = 546
            start_y = 52
            density_class = ""
        else:
            rows = math.ceil(count / 4)
            columns = math.ceil(count / rows)
            horizontal_gap = 40
            vertical_gap = 28
            card_width = (
                self._body_width - horizontal_gap * (columns - 1)
            ) // columns
            card_height = (
                self._body_height - vertical_gap * (rows - 1)
            ) // rows
            start_y = 0
            density_class = " flow-node-compact" if rows == 2 else " flow-node-dense"
        positions: dict[str, tuple[int, int, int, int]] = {}
        cards = []
        for index, step in enumerate(steps):
            row = index // columns
            local_column = index % columns
            row_count = min(columns, count - row * columns)
            display_column = (
                local_column if row % 2 == 0 else row_count - local_column - 1
            )
            row_width = card_width * row_count + horizontal_gap * (row_count - 1)
            row_start_x = (self._body_width - row_width) // 2
            x = row_start_x + display_column * (card_width + horizontal_gap)
            y = start_y + row * (card_height + vertical_gap)
            positions[step["id"]] = (x, y, card_width, card_height)
            label = self._text(
                "h2", f"flow-step-{index}-label", step["label"], expected
            )
            description = self._text(
                "p",
                f"flow-step-{index}-description",
                step["description"],
                expected,
            )
            cards.append(
                f'<article class="flow-node node-card{density_class}" '
                f'data-node-id="{html.escape(step["id"], quote=True)}" '
                f'data-x="{x}" data-row="{row}" data-column="{display_column}" '
                f'data-layout-box="flow-node-{index}" '
                f'style="left:{x}px;top:{y}px;width:{card_width}px;height:{card_height}px">'
                f'<span class="step-number">{index + 1:02d}</span>{label}{description}</article>'
            )
        edges = self._render_edges(value["edges"], positions, expected, "flow")
        return (
            '<main class="visual-body relation-layout">'
            f"{edges}{''.join(cards)}"
            "</main>"
        )

    def _render_architecture(
        self,
        value: dict[str, Any],
        expected: list[tuple[str, str]],
    ) -> str:
        declared_layers = value.get("layers", [])
        if declared_layers:
            layers = [(item["id"], item["label"], True) for item in declared_layers]
        else:
            layers = [("default", "", False)]

        nodes_by_layer: dict[str, list[dict[str, str]]] = {
            layer_id: [] for layer_id, _label, _visible in layers
        }
        for node in value["nodes"]:
            layer_id = node.get("layer", layers[0][0])
            nodes_by_layer[layer_id].append(node)

        layer_count = len(layers)
        layer_gap = 14 if layer_count <= 4 else 8 if layer_count == 5 else 4
        layer_height = (
            self._body_height - layer_gap * (len(layers) - 1)
        ) // len(layers)
        positions: dict[str, tuple[int, int, int, int]] = {}
        layer_sections = []
        node_index = 0
        for layer_index, (layer_id, layer_label, visible_label) in enumerate(layers):
            layer_y = layer_index * (layer_height + layer_gap)
            label_width = 150 if visible_label else 24
            layer_nodes = nodes_by_layer[layer_id]
            columns = min(4, len(layer_nodes))
            rows = max(1, math.ceil(len(layer_nodes) / 4))
            node_gap = 18
            nodes_width = self._body_width - label_width - 28
            node_width = (nodes_width - node_gap * (columns - 1)) // columns
            vertical_space = layer_height - 32
            node_height = (vertical_space - node_gap * (rows - 1)) // rows

            label_html = ""
            if visible_label:
                label_html = self._text(
                    "h2",
                    f"architecture-layer-{layer_index}-label",
                    layer_label,
                    expected,
                    class_name="layer-label",
                )

            cards = []
            for local_index, node in enumerate(layer_nodes):
                row = local_index // 4
                column = local_index % 4
                row_count = min(4, len(layer_nodes) - row * 4)
                row_width = node_width * row_count + node_gap * (row_count - 1)
                row_start = label_width + (nodes_width - row_width) // 2
                x = row_start + column * (node_width + node_gap)
                y = layer_y + 16 + row * (node_height + node_gap)
                positions[node["id"]] = (x, y, node_width, node_height)
                node_label = self._text(
                    "h3",
                    f"architecture-node-{node_index}-label",
                    node["label"],
                    expected,
                )
                description = self._text(
                    "p",
                    f"architecture-node-{node_index}-description",
                    node["description"],
                    expected,
                )
                cards.append(
                    '<article class="architecture-node node-card" '
                    f'data-node-id="{html.escape(node["id"], quote=True)}" '
                    f'data-layer="{html.escape(layer_id, quote=True)}" '
                    f'data-layout-box="architecture-node-{node_index}" '
                    f'style="left:{x}px;top:{y - layer_y}px;width:{node_width}px;height:{node_height}px">'
                    f"{node_label}{description}</article>"
                )
                node_index += 1
            layer_sections.append(
                '<section class="architecture-layer" '
                f'data-layer-id="{html.escape(layer_id, quote=True)}" '
                f'data-layout-box="architecture-layer-{layer_index}" '
                f'style="top:{layer_y}px;height:{layer_height}px">'
                f"{label_html}{''.join(cards)}</section>"
            )

        edges = self._render_edges(
            value["edges"], positions, expected, "architecture"
        )
        return (
            '<main class="visual-body relation-layout architecture-layout '
            f'architecture-layer-count-{layer_count}">'
            f"{edges}{''.join(layer_sections)}"
            "</main>"
        )

    def _render_comparison(
        self,
        value: dict[str, Any],
        expected: list[tuple[str, str]],
    ) -> str:
        left_label = self._text(
            "h2", "comparison-left-label", value["left"]["label"], expected
        )
        left_description = self._text(
            "p",
            "comparison-left-description",
            value["left"]["description"],
            expected,
        )
        right_label = self._text(
            "h2", "comparison-right-label", value["right"]["label"], expected
        )
        right_description = self._text(
            "p",
            "comparison-right-description",
            value["right"]["description"],
            expected,
        )
        rows = []
        for index, dimension in enumerate(value["dimensions"]):
            label = self._text(
                "h3", f"comparison-dimension-{index}-label", dimension["label"], expected
            )
            left = self._text(
                "p", f"comparison-dimension-{index}-left", dimension["left"], expected
            )
            right = self._text(
                "p", f"comparison-dimension-{index}-right", dimension["right"], expected
            )
            rows.append(
                '<section class="comparison-row" '
                f'data-layout-box="comparison-row-{index}">'
                f'<div class="comparison-value left-value">{left}</div>'
                f'<div class="dimension-label">{label}</div>'
                f'<div class="comparison-value right-value">{right}</div>'
                "</section>"
            )
        return (
            '<main class="visual-body comparison-layout">'
            '<section class="comparison-heading left-heading" data-layout-box="comparison-left">'
            f"{left_label}{left_description}</section>"
            '<div class="comparison-divider" aria-hidden="true"></div>'
            '<section class="comparison-heading right-heading" data-layout-box="comparison-right">'
            f"{right_label}{right_description}</section>"
            f'<div class="comparison-rows">{"".join(rows)}</div>'
            "</main>"
        )

    def _render_timeline(
        self,
        value: dict[str, Any],
        expected: list[tuple[str, str]],
    ) -> str:
        events = value["events"]
        count = len(events)
        gap = 30
        card_width = min(300, (self._body_width - gap * (count - 1)) // count)
        total_width = card_width * count + gap * (count - 1)
        start_x = (self._body_width - total_width) // 2
        axis_y = 321
        cards = []
        points = []
        for index, event in enumerate(events):
            x = start_x + index * (card_width + gap)
            y = 18 if index % 2 == 0 else 374
            time = self._text(
                "span", f"timeline-event-{index}-time", event["time"], expected
            )
            label = self._text(
                "h2", f"timeline-event-{index}-label", event["label"], expected
            )
            description = self._text(
                "p",
                f"timeline-event-{index}-description",
                event["description"],
                expected,
            )
            cards.append(
                '<article class="timeline-event node-card" '
                f'data-node-id="{html.escape(event["id"], quote=True)}" '
                f'data-layout-box="timeline-event-{index}" '
                f'style="left:{x}px;top:{y}px;width:{card_width}px;height:250px">'
                f"{time}{label}{description}</article>"
            )
            center_x = x + card_width // 2
            points.append(
                f'<circle cx="{center_x}" cy="{axis_y}" r="10"></circle>'
                f'<line x1="{center_x}" y1="{axis_y - 12}" x2="{center_x}" '
                f'y2="{268 if index % 2 == 0 else 374}"></line>'
            )
        return (
            '<main class="visual-body timeline-layout">'
            f'<svg class="timeline-axis" viewBox="0 0 {self._body_width} {self._body_height}" '
            'aria-hidden="true"><line x1="36" y1="321" x2="1820" y2="321"></line>'
            f'{"".join(points)}</svg>{"".join(cards)}</main>'
        )

    def _render_edges(
        self,
        edges: list[dict[str, str]],
        positions: dict[str, tuple[int, int, int, int]],
        expected: list[tuple[str, str]],
        key_prefix: str,
    ) -> str:
        paths = []
        labels = []
        for index, edge in enumerate(edges):
            source_x, source_y, source_width, source_height = positions[edge["from"]]
            target_x, target_y, target_width, target_height = positions[edge["to"]]
            x1 = source_x + source_width // 2
            y1 = source_y + source_height // 2
            x2 = target_x + target_width // 2
            y2 = target_y + target_height // 2
            if abs(x2 - x1) >= abs(y2 - y1):
                direction = 1 if x2 >= x1 else -1
                x1 += direction * source_width // 2
                x2 -= direction * target_width // 2
            else:
                direction = 1 if y2 >= y1 else -1
                y1 += direction * source_height // 2
                y2 -= direction * target_height // 2
            bend = (x1 + x2) // 2
            path = f"M{x1},{y1} C{bend},{y1} {bend},{y2} {x2},{y2}"
            source = html.escape(edge["from"], quote=True)
            target = html.escape(edge["to"], quote=True)
            paths.append(
                f'<path class="relation-edge" data-from="{source}" data-to="{target}" '
                f'd="{path}" marker-end="url(#arrowhead)"></path>'
            )
            if edge.get("label"):
                key = f"{key_prefix}-edge-{index}-label"
                label = self._text("text", key, edge["label"], expected)
                labels.append(
                    f'<g class="edge-label" transform="translate({(x1 + x2) // 2} {(y1 + y2) // 2 - 12})">'
                    f'<rect x="-72" y="-25" width="144" height="42" rx="16"></rect>{label}</g>'
                )
        return (
            f'<svg class="edge-layer" viewBox="0 0 {self._body_width} {self._body_height}" '
            'aria-label="关系连线"><defs><marker id="arrowhead" markerWidth="14" '
            'markerHeight="14" refX="11" refY="6" orient="auto" markerUnits="strokeWidth">'
            '<path d="M0,0 L12,6 L0,12 z"></path></marker></defs>'
            f'{"".join(paths)}{"".join(labels)}</svg>'
        )

    def _build_document(
        self,
        header: str,
        body: str,
        takeaways: str,
        expected: list[tuple[str, str]],
    ) -> str:
        expected_json = json.dumps(
            dict(expected), ensure_ascii=False, separators=(",", ":")
        )
        expected_json = (
            expected_json.replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={self.CANVAS_WIDTH},height={self.CANVAS_HEIGHT},initial-scale=1">
<style>
@font-face{{font-family:'Noto Sans SC';src:url('{self.FONT_FILE_NAME}') format('truetype');font-style:normal;font-weight:100 900;font-display:block}}
*{{box-sizing:border-box}}
html,body{{margin:0;width:{self.CANVAS_WIDTH}px;height:{self.CANVAS_HEIGHT}px;overflow:hidden}}
body{{font-family:'Noto Sans SC',sans-serif;color:#172033;background:#f3f6fb}}
.canvas{{position:relative;width:{self.CANVAS_WIDTH}px;height:{self.CANVAS_HEIGHT}px;overflow:hidden;background:linear-gradient(145deg,#f9fbff 0%,#f1f5fb 100%)}}
.canvas::before{{content:'';position:absolute;inset:0;background-image:radial-gradient(#cbd7e8 1px,transparent 1px);background-size:32px 32px;opacity:.2}}
.visual-header{{position:absolute;left:96px;top:64px;width:1856px;height:146px;padding-left:28px;border-left:8px solid #2463eb}}
.visual-header h1{{margin:0 0 12px;font-size:58px;line-height:1.14;letter-spacing:-1px;font-weight:760;color:#12213c}}
.visual-header p{{margin:0;font-size:32px;line-height:1.45;color:#52627b}}
.visual-body{{position:absolute;left:96px;top:230px;width:{self._body_width}px;height:{self._body_height}px}}
.takeaways{{position:absolute;left:96px;top:920px;width:1856px;height:160px;display:flex;gap:20px}}
.takeaway{{flex:1;min-width:0;height:100%;padding:28px 32px;display:flex;align-items:center;gap:20px;border:2px solid #dbe4f1;border-radius:24px;background:rgba(255,255,255,.88);box-shadow:0 12px 34px rgba(42,66,105,.06)}}
.takeaway i{{width:12px;height:52px;flex:none;border-radius:9px;background:#2463eb}}
.takeaway-text{{font-size:30px;line-height:1.45;font-weight:580;color:#253550}}
.summary-layout{{display:grid;grid-template-columns:44% 1fr;grid-template-rows:minmax(0,1fr);gap:28px;min-height:0;height:670px}}
.positioning-card,.capability-card,.comparison-heading,.comparison-row,.node-card{{border:2px solid #d7e1ef;background:rgba(255,255,255,.94);box-shadow:0 16px 42px rgba(41,65,104,.08)}}
.positioning-card{{position:relative;height:100%;padding:70px 56px;border-radius:32px;display:flex;align-items:center}}
.section-accent{{position:absolute;left:56px;top:58px;width:110px;height:8px;border-radius:8px;background:#2463eb}}
.positioning-text{{margin:0;font-size:39px;line-height:1.65;font-weight:620;color:#23334f}}
.capability-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-auto-rows:1fr;gap:22px}}
.capability-grid.capability-count-3{{grid-template-columns:1fr;grid-template-rows:repeat(3,minmax(0,1fr));gap:16px}}
.capability-card{{min-width:0;min-height:0;padding:28px 32px;border-radius:28px}}
.capability-count-3 .capability-card{{padding:20px 30px}}
.capability-card h2,.node-card h2,.node-card h3{{margin:0 0 12px;font-size:38px;line-height:1.25;color:#164fbf}}
.capability-count-3 .capability-card h2{{margin-bottom:8px}}
.capability-card p,.node-card p{{margin:0;font-size:30px;line-height:1.55;color:#52627b}}
.capability-copy-compact .capability-card p{{font-size:26px;line-height:1.4}}
.capability-copy-dense .capability-card{{padding:20px 26px}}
.capability-copy-dense .capability-card h2{{margin-bottom:8px;font-size:32px}}
.capability-copy-dense .capability-card p{{font-size:20px;line-height:1.28}}
.relation-layout{{position:absolute}}
.edge-layer{{position:absolute;inset:0;width:100%;height:100%;overflow:visible;z-index:1}}
.relation-edge{{fill:none;stroke:#6383b6;stroke-width:5;stroke-linecap:round}}
#arrowhead path{{fill:#2463eb}}
.edge-label rect{{fill:#eef4ff;stroke:#b9ccee;stroke-width:1}}
.edge-label text{{font-size:24px;text-anchor:middle;dominant-baseline:middle;fill:#34527e}}
.node-card{{position:absolute;z-index:2;border-radius:26px;padding:38px 32px}}
.flow-node{{display:flex;flex-direction:column;justify-content:center;padding:34px 32px}}
.flow-node-compact{{padding:24px 28px}}
.flow-node-compact h2{{margin-bottom:8px;font-size:34px}}
.flow-node-compact p{{font-size:26px;line-height:1.4}}
.flow-node-dense{{padding:16px 22px}}
.flow-node-dense h2{{margin-bottom:6px;font-size:28px;line-height:1.2}}
.flow-node-dense p{{font-size:21px;line-height:1.3}}
.step-number{{position:absolute;right:24px;top:20px;font-size:30px;font-weight:760;color:#b8c8df}}
.flow-node-compact .step-number{{right:18px;top:14px;font-size:25px}}
.flow-node-dense .step-number{{right:14px;top:10px;font-size:20px}}
.architecture-layer{{position:absolute;left:0;width:100%;border:2px solid #dce5f1;border-radius:26px;background:rgba(238,244,252,.6);z-index:2}}
.layer-label{{position:absolute;left:24px;top:50%;width:108px;margin:0;transform:translateY(-50%);font-size:32px;line-height:1.35;color:#34527e;text-align:center}}
.architecture-node{{padding:24px 28px;display:flex;flex-direction:column;justify-content:center}}
.architecture-node h3{{font-size:34px;margin-bottom:10px}}
.architecture-node p{{font-size:27px;line-height:1.42}}
.architecture-layer-count-5 .layer-label{{font-size:24px}}
.architecture-layer-count-5 .architecture-node{{padding:10px 16px}}
.architecture-layer-count-5 .architecture-node h3{{font-size:24px;line-height:1.2;margin-bottom:4px}}
.architecture-layer-count-5 .architecture-node p{{font-size:18px;line-height:1.2}}
.architecture-layer-count-6 .layer-label,
.architecture-layer-count-7 .layer-label{{font-size:16px;line-height:1.15}}
.architecture-layer-count-6 .architecture-node,
.architecture-layer-count-7 .architecture-node{{padding:4px 10px}}
.architecture-layer-count-6 .architecture-node h3,
.architecture-layer-count-7 .architecture-node h3{{font-size:18px;line-height:1.15;margin-bottom:1px}}
.architecture-layer-count-6 .architecture-node p,
.architecture-layer-count-7 .architecture-node p{{font-size:14px;line-height:1.15}}
.architecture-layout .edge-layer{{z-index:3}}
.architecture-layout .architecture-node{{z-index:4}}
.comparison-layout{{display:grid;grid-template-columns:1fr 6px 1fr;grid-template-rows:250px 1fr;gap:22px 28px}}
.comparison-heading{{padding:30px 42px;border-radius:26px}}
.comparison-heading h2{{margin:0 0 8px;font-size:40px;color:#164fbf}}
.comparison-heading p{{margin:0;font-size:29px;line-height:1.45;color:#52627b}}
.left-heading{{grid-column:1}}.right-heading{{grid-column:3}}
.comparison-divider{{grid-column:2;grid-row:1 / 3;border-radius:6px;background:#c9d7eb}}
.comparison-rows{{grid-column:1 / 4;display:flex;flex-direction:column;gap:14px}}
.comparison-row{{min-height:92px;flex:1;display:grid;grid-template-columns:1fr 240px 1fr;align-items:center;border-radius:22px;overflow:hidden}}
.comparison-row p,.comparison-row h3{{margin:0}}
.comparison-value{{height:100%;padding:20px 34px;display:flex;align-items:center;font-size:28px;line-height:1.4}}
.left-value{{justify-content:flex-end;text-align:right;background:#f7f9fc}}.right-value{{background:#f1f6ff}}
.dimension-label{{height:100%;display:flex;align-items:center;justify-content:center;padding:16px;background:#2463eb;color:white;text-align:center}}
.dimension-label h3{{font-size:27px;line-height:1.3}}
.timeline-axis{{position:absolute;inset:0;width:100%;height:100%;z-index:0}}
.timeline-axis>line{{stroke:#8aa3c8;stroke-width:5}}.timeline-axis circle{{fill:#2463eb;stroke:white;stroke-width:5}}
.timeline-event{{padding:28px 28px;z-index:1}}
.timeline-event span{{display:block;margin-bottom:8px;font-size:27px;font-weight:720;color:#2463eb}}
.timeline-event h2{{font-size:34px;margin-bottom:8px}}.timeline-event p{{font-size:26px;line-height:1.4}}
</style>
</head>
<body>
<div class="canvas" id="visual-canvas">{header}{body}{takeaways}</div>
<script id="expected-texts" type="application/json">{expected_json}</script>
<script>
window.__visualValidation = {{status: 'running', errors: []}};
const validateVisual = () => {{
  const errors = [];
  const canvas = document.querySelector('#visual-canvas');
  const canvasRect = canvas.getBoundingClientRect();
  const expectedTexts = JSON.parse(document.querySelector('#expected-texts').textContent);
  for (const [key, expected] of Object.entries(expectedTexts)) {{
    const element = document.querySelector(`[data-text-key="${{CSS.escape(key)}}"]`);
    if (!element || element.textContent.trim() !== expected) errors.push(`text:${{key}}`);
  }}
  for (const box of document.querySelectorAll('[data-layout-box]')) {{
    if (box.scrollWidth > box.clientWidth + 1 || box.scrollHeight > box.clientHeight + 1) {{
      errors.push(`overflow:${{box.dataset.layoutBox}}`);
    }}
    const rect = box.getBoundingClientRect();
    if (rect.left < canvasRect.left || rect.top < canvasRect.top ||
        rect.right > canvasRect.right || rect.bottom > canvasRect.bottom) {{
      errors.push(`outside:${{box.dataset.layoutBox}}`);
    }}
  }}
  const nodes = [...document.querySelectorAll('[data-node-id]')];
  for (let leftIndex = 0; leftIndex < nodes.length; leftIndex += 1) {{
    const left = nodes[leftIndex].getBoundingClientRect();
    for (let rightIndex = leftIndex + 1; rightIndex < nodes.length; rightIndex += 1) {{
      const right = nodes[rightIndex].getBoundingClientRect();
      const overlaps = left.left < right.right - 1 && left.right > right.left + 1 &&
        left.top < right.bottom - 1 && left.bottom > right.top + 1;
      if (overlaps) {{
        errors.push(`node-overlap:${{nodes[leftIndex].dataset.nodeId}}:${{nodes[rightIndex].dataset.nodeId}}`);
      }}
    }}
  }}
  if (errors.length) {{
    window.__visualValidation = {{status: 'failed', errors}};
    throw new Error(`visual-validation:${{errors.join(',')}}`);
  }}
  window.__visualValidation.errors = [];
  window.__visualValidation.status = 'passed';
}};
document.fonts.ready.then(validateVisual).catch((error) => {{
  if (window.__visualValidation.status !== 'failed') {{
    window.__visualValidation = {{status: 'failed', errors: [`runtime:${{error.message}}`]}};
  }}
  throw error;
}});
</script>
</body>
</html>"""

    @staticmethod
    def _text(
        tag: str,
        key: str,
        value: str,
        expected: list[tuple[str, str]],
        *,
        class_name: str | None = None,
    ) -> str:
        expected.append((key, value))
        class_attribute = (
            f' class="{html.escape(class_name, quote=True)}"' if class_name else ""
        )
        return (
            f'<{tag}{class_attribute} data-text-key="{html.escape(key, quote=True)}">'
            f"{html.escape(value, quote=True)}</{tag}>"
        )


__all__ = ["ArticleVisualTemplateService", "HtmlVisualDocument"]
