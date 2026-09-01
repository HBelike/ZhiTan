# Deterministic Technical Article Visuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把技术文章配图从 Seedream 概率式文字图切换为可追溯的 `ArticleVisualSpec` 和 HTML/SVG 确定性渲染，并对当前 6 项目样本完成真实 Gotenberg 生图、幂等与失败保护验证。

**Architecture:** `SummaryTask` 依据 GitHub 证据生成 `ArticleVisualSpec v1`，规划与校验服务按 baoyu 的 Type × Style × Palette 方法选择固定图型。模板服务生成带自校验脚本的 HTML/SVG，Gotenberg 截图 Provider 等待校验通过后输出 PNG；`ImageTask` 只在全部新图验证成功后通过单个数据库事务写入新资产并替换旧资产。

**Tech Stack:** Python 3.13、dataclasses、requests、Pillow、HTML/CSS、inline SVG、Gotenberg 8.34.0、Vue 3、Node test runner、Playwright 1.62.1、SQLite/PostgreSQL 兼容 Repository

**Spec:** `docs/superpowers/specs/2026-08-28-deterministic-technical-article-visuals-design.md`

## Global Constraints

- 静态技术图主路径不得调用 Seedream；Seedream 只保留为未来无文字概念背景能力，首版 `concept_background_enabled: false`。
- 输出固定为 2048×1152 PNG，`deviceScaleFactor=1`，`clip=true`。
- 图片中禁止出现 `16:9`、`16.9`、`工程架构`、`架构信息图`、`技术信息图`、`流程图`、`对比图`、`示意图`、`无标题` 等元文字。
- 中文标签不得使用字符切片静默截断；超出模板容量必须拒绝规格。
- Gotenberg 瞬时失败最多尝试 5 次；普通 4xx 和页面校验失败不重试。
- 任一待生成图片失败时，本轮不创建新资产、不替换旧资产、不生成 Seedream 文字兜底图。
- `generated_contents.image_prompts_json` 和 `media_assets.metadata_json` 承载新字段，不新增数据库表或迁移。
- 字体固定使用 Noto Sans SC，来源为 `notofonts/noto-cjk` 提交 `523d033d6cb47f4a80c58a35753646f5c3608a78`，许可证随字体入库。
- 所有新增代码注释使用中文，变量、类、函数名使用英文。
- 仅修改与本功能有关的文件；保留工作区中用户已有的其他修改。
- 只完成本地实现和验证，不部署生产、不重建生产容器。

---

## File Map

### 新增文件

- `assets/fonts/NotoSansSC-VF.ttf`：随 Gotenberg 请求上传的固定中文字体。
- `assets/fonts/Noto-CJK-LICENSE.txt`：Noto CJK 的 SIL Open Font License 文本。
- `src/services/article_visual_spec.py`：规格类型、canonical JSON 与 `render_key`。
- `src/services/article_visual_spec_validator.py`：结构、证据、禁词、容量和拓扑校验。
- `src/services/article_visual_planning_service.py`：baoyu 图型选择契约、模型输出规范化、旧内容降级和视频兼容转换。
- `src/services/article_visual_template_service.py`：五种固定 HTML/SVG 模板及浏览器内校验脚本。
- `src/providers/gotenberg_screenshot_provider.py`：Gotenberg multipart、五次重试和原子写入。
- `src/services/rendered_visual_validator.py`：PNG 解码、格式与尺寸校验。
- `src/services/article_visual_generation_service.py`：规格校验、幂等判断、批量渲染和资产输入构建。
- `web-ui/src/article-visual.js`：前端图型和校验状态的中文展示函数。
- `web-ui/src/article-visual.test.js`：前端展示函数测试。
- `tests/test_image_renderer_config.py`：配置与字体固定契约。
- `tests/test_article_visual_spec.py`：规划、规格、拓扑、禁词和 render key 测试。
- `tests/test_article_visual_template_service.py`：五种模板、转义和自校验标记测试。
- `tests/test_gotenberg_screenshot_provider.py`：请求字段、重试、PNG 与原子写入测试。
- `tests/test_article_visual_generation_service.py`：六图、幂等、批次失败和资产 metadata 测试。
- `tests/test_media_preview_visual_spec.py`：后端预览 payload 测试。
- `scripts/verify_deterministic_article_visuals.py`：指定 `content_id` 的真实渲染和验收脚本。

### 修改文件

- `config/app.yaml`：增加 renderer/Gotenberg/画布/字体配置，废止静态图 prompt 主路径。
- `src/config/config_manager.py`：读取并严格校验新配置。
- `src/tasks/summary_task.py`：输出并验证 `visual_spec`，移除静态图强制架构契约。
- `src/services/media_creative_brief_service.py`：移除 6 字标签截断，仅保留视频兼容用途。
- `src/repositories/generated_content_repository.py`：透传 `visual_spec`。
- `src/repositories/media_asset_repository.py`：新增批量创建与替换的单事务接口。
- `src/tasks/image_task.py`：确定性渲染主路径、指定内容入口和新统计字段。
- `src/services/media_preview_service.py`：展示规格、模板、render key 和校验结果。
- `web-ui/src/App.vue`：把图片 prompt 卡改为图片总结方案卡。
- `tests/test_summary_visual_brief.py`：改为 visual spec 和视频兼容断言。
- `tests/test_summary_generation_orchestration.py`：更新模型合同和摘要编排断言。
- `tests/test_dynamic_project_image_count.py`：从 Seedream 六图测试改为确定性六图测试。
- `tests/test_image_prompt_design_service.py`：删除静态图 prompt 主路径断言，保留 Seedream legacy 单元边界。
- `docs/weekly_media_pipeline.md`：记录新调用链、重试、失败语义和真实结果。
- `docs/media_creative_direction.md`：记录 Type × Style × Palette、模板容量和禁词。
- `docs/image_prompt_skill_research.md`：记录 baoyu Skill 的使用边界。

---

### Task 1: 固定渲染配置与中文字体

**Files:**
- Create: `assets/fonts/NotoSansSC-VF.ttf`
- Create: `assets/fonts/Noto-CJK-LICENSE.txt`
- Create: `tests/test_image_renderer_config.py`
- Modify: `config/app.yaml:58-95`
- Modify: `src/config/config_manager.py:200-365, 930-982`

**Interfaces:**
- Consumes: 现有 `ConfigManager.load()` 与 `AppConfig.raw`。
- Produces: `AppConfig.image_renderer`、`image_gotenberg_base_url`、`image_gotenberg_timeout_seconds`、`image_renderer_max_attempts`、`image_canvas_width`、`image_canvas_height`、`image_template_version`、`image_renderer_version`、`image_font_path`、`image_font_version`、`image_concept_background_enabled`。

- [ ] **Step 1: 写配置失败测试**

```python
def test_deterministic_image_renderer_config_is_complete(monkeypatch):
    monkeypatch.delenv("CAREER_GOTENBERG_SERVICE_BASE_URL", raising=False)
    config = ConfigManager(PROJECT_ROOT).load()
    assert config.image_renderer == "gotenberg_html"
    assert config.image_gotenberg_base_url == "http://127.0.0.1:3000"
    assert config.image_canvas_width == 2048
    assert config.image_canvas_height == 1152
    assert config.image_renderer_max_attempts == 5
    assert config.image_font_path.name == "NotoSansSC-VF.ttf"
    assert config.image_font_version == "noto-cjk-2.004-523d033d"
    assert config.image_concept_background_enabled is False


def test_gotenberg_environment_url_wins(monkeypatch):
    monkeypatch.setenv("CAREER_GOTENBERG_SERVICE_BASE_URL", "http://career-gotenberg:3000")
    assert ConfigManager(PROJECT_ROOT).load().image_gotenberg_base_url == "http://career-gotenberg:3000"
```

- [ ] **Step 2: 运行测试并确认因属性不存在而失败**

Run: `python -m pytest tests/test_image_renderer_config.py -v`

Expected: FAIL，首个错误为 `AppConfig` 没有 `image_renderer`。

- [ ] **Step 3: 固定字体和许可证来源**

Run:

```powershell
New-Item -ItemType Directory -Force assets/fonts
curl.exe -L "https://raw.githubusercontent.com/notofonts/noto-cjk/523d033d6cb47f4a80c58a35753646f5c3608a78/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf" -o "assets/fonts/NotoSansSC-VF.ttf"
curl.exe -L "https://raw.githubusercontent.com/notofonts/noto-cjk/523d033d6cb47f4a80c58a35753646f5c3608a78/LICENSE" -o "assets/fonts/Noto-CJK-LICENSE.txt"
```

Verify:

```powershell
Get-Item assets/fonts/NotoSansSC-VF.ttf | Select-Object Name,Length
Select-String -Path assets/fonts/Noto-CJK-LICENSE.txt -Pattern "SIL OPEN FONT LICENSE"
```

Expected: 字体大于 10 MB，许可证匹配 `SIL OPEN FONT LICENSE`。

- [ ] **Step 4: 增加精确配置和校验**

在 `config/app.yaml` 的 `image` 下加入：

```yaml
  renderer:
    name: gotenberg_html
    gotenberg_base_url: http://127.0.0.1:3000
    timeout_seconds: 60
    max_attempts: 5
    width: 2048
    height: 1152
    template_version: article_visual_v1
    renderer_version: gotenberg_html_v1
    font_path: assets/fonts/NotoSansSC-VF.ttf
    font_version: noto-cjk-2.004-523d033d
    concept_background_enabled: false
```

在 `AppConfig` 中通过 `_image_renderer_raw()` 读取该段；`image_gotenberg_base_url` 优先读取 `CAREER_GOTENBERG_SERVICE_BASE_URL`。`ConfigManager._validate()` 拒绝非 `gotenberg_html|seedream` renderer、非 2048×1152 画布、非 5 次重试、非正超时、空版本和不存在字体。

- [ ] **Step 5: 运行配置测试和现有配置回归**

Run: `python -m pytest tests/test_image_renderer_config.py tests/test_pipeline_runtime_config.py tests/test_seedream_provider.py -v`

Expected: PASS；Seedream Provider 的五次重试测试继续通过。

- [ ] **Step 6: 提交配置与字体**

```bash
git add assets/fonts config/app.yaml src/config/config_manager.py tests/test_image_renderer_config.py
git commit -m "feat: configure deterministic article image renderer"
```

---

### Task 2: 建立 ArticleVisualSpec、规划与校验合同

**Files:**
- Create: `src/services/article_visual_spec.py`
- Create: `src/services/article_visual_spec_validator.py`
- Create: `src/services/article_visual_planning_service.py`
- Create: `tests/test_article_visual_spec.py`

**Interfaces:**
- Consumes: 模型输出字典、仓库名、已知证据路径、旧 `image_prompts` 项。
- Produces: `ValidatedArticleVisualSpec`、`ArticleVisualSpecValidator.validate()`、`ArticleVisualPlanningService.plan()`、`plan_legacy_content_brief()`、`to_video_visual_brief()`、`build_render_key()`。

- [ ] **Step 1: 写五类图型、禁词和拓扑失败测试**

```python
def test_flow_spec_keeps_complete_chinese_labels_and_edges():
    spec = validator.validate(
        raw_spec=flow_spec(label="统一LLM适配层"),
        repository_full_name="owner/project",
        allowed_evidence_paths={"README.md"},
    )
    assert spec.value["steps"][1]["label"] == "统一LLM适配层"
    assert spec.edges == (("spec", "adapter"), ("adapter", "output"))


@pytest.mark.parametrize("phrase", ["16:9", "16.9", "工程架构", "无标题", "流程图"])
def test_meta_words_are_rejected(phrase):
    with pytest.raises(ArticleVisualSpecError, match="元文字"):
        validator.validate(summary_spec(headline=phrase), "owner/project", {"README.md"})


def test_dangling_edge_is_rejected():
    raw = flow_spec(label="统一LLM适配层")
    raw["edges"][1]["to"] = "missing"
    with pytest.raises(ArticleVisualSpecError, match="无效节点"):
        validator.validate(raw, "owner/project", {"README.md"})
```

再覆盖 `summary_card`、`architecture`、`comparison`、`timeline` 的合法最小规格，以及空证据、重复节点、自环、重复边、超容量文字和未知字段。

- [ ] **Step 2: 运行规格测试并确认模块不存在**

Run: `python -m pytest tests/test_article_visual_spec.py -v`

Expected: FAIL with `ModuleNotFoundError: src.services.article_visual_spec`。

- [ ] **Step 3: 实现稳定规格类型和 render key**

```python
@dataclass(frozen=True)
class ValidatedArticleVisualSpec:
    value: dict[str, Any]
    canonical_json: str
    repository_full_name: str
    figure_role: str
    visible_texts: tuple[str, ...]
    node_ids: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]


def build_render_key(
    spec: ValidatedArticleVisualSpec,
    template_version: str,
    renderer_version: str,
    font_version: str,
) -> str:
    source = "\n".join((spec.canonical_json, template_version, renderer_version, font_version))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
```

canonical JSON 使用 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`；规格对象只包含校验后字段。

- [ ] **Step 4: 实现结构、证据、禁词与容量校验**

`ArticleVisualSpecValidator.validate(raw_spec, repository_full_name, allowed_evidence_paths=None)` 必须：

1. 拒绝未知公共字段和未知 role 字段。
2. 要求 `version == article_visual_spec_v1`、仓库名一致、1–3 条 takeaways、至少一条 evidence ref。
3. 当 `allowed_evidence_paths` 非空时，要求每个 ref 的 `path` 属于集合。
4. 递归扫描所有可见文字中的禁词和空占位符。
5. 按 role 验证数量：summary capabilities 2–4；flow steps 按实际证据动态确定且 edges 始终为相邻顺序链；architecture nodes 3–7；comparison dimensions 2–4；timeline events 3–6。
6. 校验 Unicode 标签长度与模板容量，不修改原文字。
7. 产出节点、边和可见文字清单。

- [ ] **Step 5: 实现 baoyu 规划规则和旧数据降级**

```python
class ArticleVisualPlanningService:
    def plan(
        self,
        raw_spec: dict[str, Any],
        repository_full_name: str,
        allowed_evidence_paths: set[str] | None,
    ) -> ValidatedArticleVisualSpec:
        return self.validator.validate(raw_spec, repository_full_name, allowed_evidence_paths)

    def plan_legacy_content_brief(self, item: dict[str, Any]) -> ValidatedArticleVisualSpec:
        summary_text = str(item.get("summary_text") or item.get("project_summary_text") or "").strip()
        raw_spec = self._build_legacy_summary_card(item, summary_text)
        return self.validator.validate(raw_spec, str(item["repository_full_name"]), None)
```

旧内容一律降级为 `summary_card`，证据 ref 使用 `project_analysis_markdown`；不复用已经截断的旧节点标签，不从旧 `visual_brief` 猜测拓扑。`to_video_visual_brief()` 只把新规格已有的节点和边映射给视频链路；summary/comparison 不补造边。

`_build_legacy_summary_card()` 的字段来源固定如下，任何必需正文缺失都抛错，不生成通用占位内容：

```python
{
    "version": "article_visual_spec_v1",
    "repository_full_name": repository_full_name,
    "figure_role": "summary_card",
    "purpose": "总结该项目的定位、机制和工程价值",
    "headline": repository_full_name.split("/", 1)[-1],
    "evidence_refs": [{
        "kind": "generated_article",
        "path": "project_analysis_markdown",
        "claim": summary_text,
    }],
    "positioning": summary_text,
    "capabilities": self._legacy_section_cards(project_analysis_markdown),
    "takeaways": self._legacy_takeaways(project_analysis_markdown),
    "art_direction": {"style": "notion", "palette": "editorial_blue", "density": "medium"},
}
```

`_legacy_section_cards()` 从 `技术特点/机制拆解/工程启发` 三个固定段各取第一句，保留完整句子并返回 `{label, description}`；`_legacy_takeaways()` 取其中 1–3 句。不得字符切片，超过容量由 validator 拒绝。

- [ ] **Step 6: 运行规格测试**

Run: `python -m pytest tests/test_article_visual_spec.py -v`

Expected: PASS，且 `统一LLM适配层` 原样保留。

- [ ] **Step 7: 提交规格合同**

```bash
git add src/services/article_visual_spec.py src/services/article_visual_spec_validator.py src/services/article_visual_planning_service.py tests/test_article_visual_spec.py
git commit -m "feat: add evidence-backed article visual specs"
```

---

### Task 3: 将 SummaryTask 与持久化切换到 visual_spec

**Files:**
- Modify: `src/tasks/summary_task.py:293-476, 680-806`
- Modify: `src/services/media_creative_brief_service.py:56-108, 329-337`
- Modify: `src/repositories/generated_content_repository.py:514-559`
- Modify: `tests/test_summary_visual_brief.py`
- Modify: `tests/test_summary_generation_orchestration.py`
- Modify: `tests/test_image_prompt_design_service.py`

**Interfaces:**
- Consumes: Task 2 的 `ArticleVisualPlanningService` 和 `ValidatedArticleVisualSpec.value`。
- Produces: 每个 `image_prompts` 项中的 `visual_spec`、兼容 `visual_brief`、`prompt_stage=article_visual_spec_v1`。

- [ ] **Step 1: 把摘要测试夹具改为 visual_spec 并先观察失败**

项目模型返回合同改为：

```python
"project_brief": {
    "summary_text": "规格先成为可执行输入，再经过计划与任务拆解进入实现。",
    "visual_spec": {
        "version": "article_visual_spec_v1",
        "repository_full_name": ranking.full_name,
        "figure_role": "flow",
        "purpose": "解释规格如何转化为可执行任务",
        "headline": "从规格到可执行任务",
        "evidence_refs": [{"kind": "repository_file", "path": "README.md", "claim": "README 给出阶段顺序"}],
        "steps": [
            {"id": "spec", "label": "需求规格", "description": "明确目标与约束"},
            {"id": "plan", "label": "技术计划", "description": "拆分交付步骤"},
            {"id": "task", "label": "任务清单", "description": "形成执行清单"}
        ],
        "edges": [{"from": "spec", "to": "plan"}, {"from": "plan", "to": "task"}],
        "takeaways": ["先约束再实现"],
        "art_direction": {"style": "notion", "palette": "editorial_blue", "density": "medium"}
    }
}
```

Run: `python -m pytest tests/test_summary_visual_brief.py tests/test_summary_generation_orchestration.py -v`

Expected: FAIL，错误指出 `project_brief` 字段仍要求 `visual_brief`。

- [ ] **Step 2: 修改单项目 LLM 合同和证据路径**

`_build_project_messages()` 明确五种 `figure_role` 的选择顺序、固定公共字段和专属字段；禁止输出图片格式元文字。`_normalize_project_output()` 新增 `ranking_evidence` 参数，并建立：

```python
allowed_evidence_paths = {"weekly_ranking", "repository.description", "repository.topics", "repository.license"}
if ranking_evidence.readme_excerpt.strip():
    allowed_evidence_paths.add("README.md")
```

把 `raw_brief["visual_spec"]` 传给 planning service；规格错误沿用现有单项目 repair 调用，不影响其他已通过项目。

- [ ] **Step 3: 修改 content brief 和视频兼容字段**

每个 item 保存：

```python
{
    "repository_full_name": ranking.full_name,
    "rank": ranking.rank,
    "summary_text": summary_text,
    "project_summary_text": summary_text,
    "project_analysis_markdown": project_analysis,
    "prompt": visual_spec["purpose"],
    "raw_prompt": visual_spec["purpose"],
    "prompt_stage": "article_visual_spec_v1",
    "visual_spec": visual_spec,
    "visual_brief": planning_service.to_video_visual_brief(validated_spec),
    "video_brief": video_brief,
}
```

`_build_content_briefs()` 用 `planning_service.plan(raw_project_brief["visual_spec"], ranking.full_name, allowed_evidence_paths=None)` 恢复 `validated_spec`，然后执行：

```python
compat_visual_brief = planning_service.to_video_visual_brief(validated_spec)
video_brief = creative_brief_service.normalize_video_brief(
    raw_brief=None,
    visual_brief=compat_visual_brief,
    project_summary_text=summary_text,
    repository_full_name=ranking.full_name,
    project_index=index,
)
```

删除静态图必须 3–4 节点、2–4 关系和 `数据流` 的断言。`MediaCreativeBriefService._short_label()` 返回清理后的最多 12 字标签，不再 `label[:6]`。

- [ ] **Step 4: 让 Repository 透传新字段**

在 `_normalize_image_prompts()` 的 optional fields 增加 `visual_spec`；保持旧 `visual_brief`、`prompt` 和 `video_brief` 兼容。

- [ ] **Step 5: 运行摘要、持久化和视频相关回归**

Run:

```powershell
python -m pytest tests/test_summary_visual_brief.py tests/test_summary_generation_orchestration.py tests/test_image_prompt_design_service.py tests/test_dynamic_project_image_count.py -v
```

Expected: PASS；测试明确覆盖 summary card 降级、flow 规格、标签不截断和视频兼容字段。

- [ ] **Step 6: 提交摘要合同**

```bash
git add src/tasks/summary_task.py src/services/media_creative_brief_service.py src/repositories/generated_content_repository.py tests/test_summary_visual_brief.py tests/test_summary_generation_orchestration.py tests/test_image_prompt_design_service.py
git commit -m "feat: generate article visual specs from repository evidence"
```

---

### Task 4: 实现五种固定 HTML/SVG 模板

**Files:**
- Create: `src/services/article_visual_template_service.py`
- Create: `tests/test_article_visual_template_service.py`

**Interfaces:**
- Consumes: Task 2 的 `ValidatedArticleVisualSpec`、字体文件名和固定画布。
- Produces: `HtmlVisualDocument(html, expected_texts, node_ids, edges)` 与 `ArticleVisualTemplateService.render(spec)`。

- [ ] **Step 1: 写模板结构与转义失败测试**

```python
@pytest.mark.parametrize("figure_role", ["summary_card", "flow", "architecture", "comparison", "timeline"])
def test_each_role_renders_a_self_validating_document(figure_role):
    document = service.render(validated_fixture(figure_role))
    assert '<meta charset="utf-8">' in document.html
    assert "NotoSansSC-VF.ttf" in document.html
    assert "window.__visualValidation" in document.html
    assert "status = 'passed'" in document.html
    assert "16:9" not in document.html
    assert document.expected_texts


def test_visible_text_is_html_escaped():
    document = service.render(validated_summary(headline="A < B & C"))
    assert "A &lt; B &amp; C" in document.html
    assert "A < B & C" not in document.html
```

flow 和 architecture 测试还要逐条断言 `data-from`、`data-to` 和唯一 `marker-end`。

- [ ] **Step 2: 运行模板测试并确认模块不存在**

Run: `python -m pytest tests/test_article_visual_template_service.py -v`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现固定 HTML 外壳和 role 分发**

```python
@dataclass(frozen=True)
class HtmlVisualDocument:
    html: str
    expected_texts: tuple[tuple[str, str], ...]
    node_ids: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]


class ArticleVisualTemplateService:
    def render(self, spec: ValidatedArticleVisualSpec) -> HtmlVisualDocument:
        body = {
            "summary_card": self._render_summary_card,
            "flow": self._render_flow,
            "architecture": self._render_architecture,
            "comparison": self._render_comparison,
            "timeline": self._render_timeline,
        }[spec.figure_role](spec.value)
        return self._build_document(spec, body)
```

模板位置固定：summary 为定位 + 能力卡 + 结论；flow 按实际步骤数动态使用单行或蛇形多行布局；architecture 为 2–3 层网格；comparison 为双列共同维度；timeline 为横向时间轴。所有文本使用 `html.escape(..., quote=True)`。

- [ ] **Step 4: 实现浏览器内验证脚本**

HTML 初始设置 `window.__visualValidation = {status: 'running', errors: []}`。`document.fonts.ready` 后检查：

```javascript
const boxes = [...document.querySelectorAll('[data-layout-box]')]
const errors = []
const expectedTexts = JSON.parse(document.querySelector('#expected-texts').textContent)
for (const [key, expected] of Object.entries(expectedTexts)) {
  const element = document.querySelector(`[data-text-key="${CSS.escape(key)}"]`)
  if (!element || element.textContent.trim() !== expected) {
    errors.push(`text:${key}`)
  }
}
for (const box of boxes) {
  if (box.scrollWidth > box.clientWidth || box.scrollHeight > box.clientHeight) {
    errors.push(`overflow:${box.dataset.layoutBox}`)
  }
  const rect = box.getBoundingClientRect()
  if (rect.left < 0 || rect.top < 0 || rect.right > 2048 || rect.bottom > 1152) {
    errors.push(`outside:${box.dataset.layoutBox}`)
  }
}
if (errors.length) {
  window.__visualValidation = { status: 'failed', errors }
  throw new Error(`visual-validation:${errors.join(',')}`)
}
window.__visualValidation = { status: 'passed', errors: [] }
```

`#expected-texts` 使用 `type="application/json"`，内容来自 `dict(HtmlVisualDocument.expected_texts)` 的 JSON 映射并转义 `<`。对节点包围盒增加两两重叠检查；合法的父容器嵌套不参与节点重叠计算。

- [ ] **Step 5: 运行模板测试**

Run: `python -m pytest tests/test_article_visual_template_service.py -v`

Expected: PASS；五类模板均有稳定 data 属性，输入无法注入 HTML/JS。

- [ ] **Step 6: 提交模板**

```bash
git add src/services/article_visual_template_service.py tests/test_article_visual_template_service.py
git commit -m "feat: render fixed technical article visual templates"
```

---

### Task 5: 实现 Gotenberg Provider、五次重试和 PNG 校验

**Files:**
- Create: `src/providers/gotenberg_screenshot_provider.py`
- Create: `src/services/rendered_visual_validator.py`
- Create: `tests/test_gotenberg_screenshot_provider.py`

**Interfaces:**
- Consumes: Task 1 配置、Task 4 `HtmlVisualDocument`、字体路径和输出路径。
- Produces: `GotenbergScreenshotResult(output_path, trace_id, attempts)` 与 `GotenbergScreenshotProvider.render()`。

- [ ] **Step 1: 写 multipart、重试与原子文件失败测试**

```python
def test_request_uses_strict_screenshot_contract(tmp_path):
    provider = build_provider(session_responses=[png_response(2048, 1152)])
    result = provider.render(document(), tmp_path / "result.png")
    fields = provider.session.post.call_args.kwargs["data"]
    assert fields == {
        "width": "2048",
        "height": "1152",
        "clip": "true",
        "deviceScaleFactor": "1",
        "format": "png",
        "waitForExpression": "window.__visualValidation?.status === 'passed'",
        "failOnConsoleExceptions": "true",
        "failOnResourceLoadingFailed": "true",
    }
    assert result.output_path.exists()
    assert not (tmp_path / "result.png.part").exists()


def test_transient_failures_stop_after_five_attempts(tmp_path):
    provider = build_provider(session_responses=[requests.ConnectionError("断开")] * 5)
    with pytest.raises(GotenbergScreenshotError, match="已尝试 5 次"):
        provider.render(document(), tmp_path / "result.png")
```

另测 400 只请求一次、503 后成功、无效 PNG、错误尺寸和现有目标文件不被失败响应覆盖。

- [ ] **Step 2: 运行 Provider 测试并确认模块不存在**

Run: `python -m pytest tests/test_gotenberg_screenshot_provider.py -v`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现 PNG 校验器**

```python
class RenderedVisualValidator:
    def validate_png_bytes(self, payload: bytes, width: int, height: int) -> None:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.format != "PNG":
                raise RenderedVisualValidationError("Gotenberg 响应不是 PNG")
            if image.size != (width, height):
                raise RenderedVisualValidationError(
                    f"图片尺寸错误：expected={width}x{height} actual={image.width}x{image.height}"
                )
```

- [ ] **Step 4: 实现 Gotenberg 请求与重试**

Provider 创建 `requests.Session()` 后设置 `trust_env=False`，避免 Windows 系统代理影响本机或 Docker 私网服务。multipart 中 `index.html` 必须命名为 `index.html`，字体命名为 `NotoSansSC-VF.ttf`。重试状态仅限 408、429、500、502、503、504 和 `Timeout/ConnectionError`，退避为 1、2、4、8 秒；400、409 直接返回页面校验错误。

请求 URL 固定为 `config.image_gotenberg_base_url.rstrip("/") + "/forms/chromium/screenshot/html"`。

响应先在内存中通过 PNG 校验，再写到同目录 `output.name + '.part'`，使用 `os.replace(part_path, output_path)` 完成原子落盘，`finally` 删除本次创建的 `.part`。

- [ ] **Step 5: 运行 Provider 与 Seedream 重试回归**

Run: `python -m pytest tests/test_gotenberg_screenshot_provider.py tests/test_seedream_provider.py -v`

Expected: PASS；两个 Provider 都最多尝试 5 次，Gotenberg 不继承环境代理。

- [ ] **Step 6: 提交 Provider**

```bash
git add src/providers/gotenberg_screenshot_provider.py src/services/rendered_visual_validator.py tests/test_gotenberg_screenshot_provider.py
git commit -m "feat: add validated Gotenberg screenshot provider"
```

---

### Task 6: 批量接入 ImageTask、render key 与事务替换

**Files:**
- Create: `src/services/article_visual_generation_service.py`
- Create: `tests/test_article_visual_generation_service.py`
- Modify: `src/repositories/media_asset_repository.py:319-346`
- Modify: `src/tasks/image_task.py:34-299, 861-950`
- Modify: `tests/test_dynamic_project_image_count.py:60-150`

**Interfaces:**
- Consumes: Task 2–5 服务、`GeneratedContentForImage`、现有图片资产。
- Produces: `PreparedArticleVisualAsset`、`ArticleVisualGenerationBatch`、`ArticleVisualGenerationService.prepare()`、`MediaAssetRepository.create_and_replace_images()`、`ImageTask.execute_for_content()`。

- [ ] **Step 1: 写六图、幂等和关闭式失败测试**

```python
def test_prepares_one_deterministic_image_for_each_of_six_projects(tmp_path):
    batch = service.prepare(content=six_project_content(), existing_assets=[])
    assert len(batch.new_assets) == 6
    assert batch.reused_assets == ()
    assert provider.render.call_count == 6
    assert {item.input.provider for item in batch.new_assets} == {"gotenberg_html"}
    assert all(item.input.metadata["render_key"] for item in batch.new_assets)


def test_matching_render_keys_reuse_all_six_images():
    batch = service.prepare(content=six_project_content(), existing_assets=matching_assets())
    assert len(batch.reused_assets) == 6
    assert batch.new_assets == ()
    provider.render.assert_not_called()


def test_sixth_render_failure_produces_no_database_mutation(tmp_path):
    old_assets = existing_assets()
    provider.render.side_effect = [render_result(tmp_path, index) for index in range(5)] + [RuntimeError("校验失败")]
    with pytest.raises(RuntimeError, match="校验失败"):
        image_task.execute_for_content(context, content_id=20)
    media_repository.create_and_replace_images.assert_not_called()
    assert [asset.status for asset in old_assets] == ["created"] * 6
```

测试文件中的 `render_result(tmp_path, index)` 用 Pillow 创建一张 2048×1152 PNG，并返回 `GotenbergScreenshotResult(output_path=path, trace_id=f"trace-{index}", attempts=1)`；`matching_assets()` 为 6 条 provider=`gotenberg_html`、文件存在且 render key 与规格相同的 `MediaAssetRecord`。

- [ ] **Step 2: 运行批量测试并确认服务不存在**

Run: `python -m pytest tests/test_article_visual_generation_service.py tests/test_dynamic_project_image_count.py -v`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现 generation service 与稳定文件名**

```python
@dataclass(frozen=True)
class PreparedArticleVisualAsset:
    input: MediaAssetInput
    render_key: str
    repository_full_name: str
    output_path: Path


@dataclass(frozen=True)
class ArticleVisualGenerationBatch:
    new_assets: tuple[PreparedArticleVisualAsset, ...]
    reused_assets: tuple[MediaAssetRecord, ...]
    replace_asset_ids: tuple[int, ...]
```

`prepare()` 先验证所有规格，再计算所有 render keys，然后才开始网络渲染。新文件名为：

```python
f"{index:02d}_{safe_repository}_{render_key[:12]}.png"
```

已有资产只有在文件存在、provider 为 `gotenberg_html` 且 metadata render key 完全一致时复用。缺少 `visual_spec` 的历史 item 调用 `plan_legacy_content_brief()` 生成 summary card。

- [ ] **Step 4: 实现单事务创建与替换**

```python
def create_and_replace_images(
    self,
    assets: list[MediaAssetInput],
    replace_asset_ids: list[int],
) -> list[MediaAssetRecord]:
    with self.database_manager.connection() as conn:
        created_ids = [self._insert_with_connection(conn, asset) for asset in assets]
        self._mark_replaced_with_connection(conn, replace_asset_ids)
        rows = self._select_by_ids_with_connection(conn, created_ids)
    return [self._row_to_record(row) for row in rows]
```

把现有 row-to-record 和 insert SQL 提取为以下私有 helper：`_insert_with_connection(conn, asset) -> int`、`_mark_replaced_with_connection(conn, asset_ids) -> int`、`_select_by_ids_with_connection(conn, asset_ids) -> list[Any]`、`_row_to_record(row) -> MediaAssetRecord`。保证 SQLite/PostgreSQL 适配层仍由 `DatabaseManager.connection()` 管理。没有新资产时不得标记旧资产。

- [ ] **Step 5: 切换 ImageTask 主路径**

`execute()` 调用 `execute_for_content(context, content_id=None)`；指定 ID 时使用 `get_for_image_generation()`，否则使用 latest。`image_renderer == gotenberg_html` 时：

```python
def execute_for_content(
    self,
    context: TaskContext,
    content_id: int | None = None,
) -> dict[str, Any]:
    content = (
        content_repository.get_for_image_generation(content_id)
        if content_id is not None
        else content_repository.latest_for_image_generation()
    )
    if content is None:
        raise RuntimeError(f"没有可生成图片的内容：content_id={content_id}")
    return self._execute_deterministic(context, content)
```

1. 始终使用 SummaryTask 的 content prompts，不选择 storyboard architecture prompts。
2. 不检查 Seedream API key，不进入 GitHub/local fallback。
3. `prepare()` 全部成功后调用一次 `create_and_replace_images()`。
4. 数据库事务失败时只删除本批次新 render key 对应文件。
5. 返回 `renderer=gotenberg_html`、`rendered_image_count`、`reused_image_count`、`validation_failed_count=0`、`figure_role_counts`。

保留显式 `image_renderer == seedream` 的旧分支供历史调试，但默认配置和验收不得进入该分支。

- [ ] **Step 6: 运行 ImageTask、数据库和动态项目测试**

Run:

```powershell
python -m pytest tests/test_article_visual_generation_service.py tests/test_dynamic_project_image_count.py tests/test_media_channel_disable.py tests/test_seedream_provider.py -v
```

Expected: PASS；六图只调用 Gotenberg mock，不调用 Seedream/GitHub/local provider。

- [ ] **Step 7: 提交编排与事务**

```bash
git add src/services/article_visual_generation_service.py src/repositories/media_asset_repository.py src/tasks/image_task.py tests/test_article_visual_generation_service.py tests/test_dynamic_project_image_count.py
git commit -m "feat: render article images transactionally with Gotenberg"
```

---

### Task 7: 更新媒体预览和 PC 图片方案页

**Files:**
- Create: `tests/test_media_preview_visual_spec.py`
- Create: `web-ui/src/article-visual.js`
- Create: `web-ui/src/article-visual.test.js`
- Modify: `src/services/media_preview_service.py:169-270, 513-540`
- Modify: `web-ui/src/App.vue:148-158, 212-220, 372-378, 2118-2148`

**Interfaces:**
- Consumes: 资产 metadata 中的 `visual_spec`、`figure_role`、`template_version`、`render_key`、`validation_result`。
- Produces: 后端 `visual_spec` 预览字段，以及前端 `figureRoleLabel()`、`visualSpecSummary()`、`visualValidationLabel()`。

- [ ] **Step 1: 写后端预览失败测试**

```python
def test_deterministic_asset_overrides_legacy_prompt_display():
    prompts = service._build_effective_image_prompts(
        content_id=20,
        image_prompts=[legacy_prompt()],
        media_assets=[gotenberg_asset()],
    )
    item = prompts[0]
    assert item["prompt_stage"] == "deterministic_rendered"
    assert item["visual_spec"]["figure_role"] == "summary_card"
    assert item["template_version"] == "article_visual_v1"
    assert item["validation_result"]["status"] == "passed"
    assert item["prompt_designed_by"] == "ArticleVisualTemplateService"
```

Run: `python -m pytest tests/test_media_preview_visual_spec.py -v`

Expected: FAIL，现有逻辑把资产归类为 fallback。

- [ ] **Step 2: 修改后端关联逻辑**

在 Seedream/fallback 之前识别 provider `gotenberg_html` 且 metadata 含 `render_key` 的资产。返回 `visual_spec`、`figure_role`、`template_version`、`renderer_version`、`render_key`、`validation_result` 和 `asset_id`；旧资产继续沿用原分支。

- [ ] **Step 3: 写前端纯函数失败测试**

```javascript
test('图片方案使用中文图型和确定性校验状态', () => {
  assert.equal(figureRoleLabel('summary_card'), '总结卡')
  assert.equal(figureRoleLabel('architecture'), '模块关系')
  assert.equal(visualValidationLabel({ status: 'passed' }), '校验通过')
  assert.equal(visualSpecSummary({ headline: '从规格到任务', purpose: '解释交付链路' }), '从规格到任务 · 解释交付链路')
})
```

Run: `npm test -- --test-name-pattern="图片方案"`，workdir `web-ui`

Expected: FAIL，模块不存在。

- [ ] **Step 4: 实现前端映射并改造现有 PC 页面**

`article-visual.js` 对五种 role 映射为 `总结卡/步骤流程/模块关系/方案对比/演进时间线`。`App.vue` 把导航和卡片中的“生成提示词/生图提示词”改为“生成方案/图片总结方案”，每张图显示：图型、headline、purpose、1–3 条 takeaways、模板版本和校验状态；不再把确定性图显示为 Seedream prompt。视频列保持原有结构，不增加新路由或手机专项样式。

- [ ] **Step 5: 运行后端、前端与构建验证**

Run:

```powershell
python -m pytest tests/test_media_preview_visual_spec.py -v
npm test
npm run build
```

后两条命令 workdir 为 `web-ui`。Expected: 全部 PASS，Vite 构建成功。

- [ ] **Step 6: 提交预览**

```bash
git add src/services/media_preview_service.py tests/test_media_preview_visual_spec.py web-ui/src/article-visual.js web-ui/src/article-visual.test.js web-ui/src/App.vue
git commit -m "feat: show deterministic image plans in workbench"
```

---

### Task 8: 真实 Gotenberg 六图验收、全量回归和文档

**Files:**
- Create: `scripts/verify_deterministic_article_visuals.py`
- Modify: `docs/weekly_media_pipeline.md`
- Modify: `docs/media_creative_direction.md`
- Modify: `docs/image_prompt_skill_research.md`

**Interfaces:**
- Consumes: Task 1–7 的完整链路、本地 `data/app.db`、Gotenberg `http://127.0.0.1:3000`。
- Produces: `outputs/visual-verification/content-<id>.json` 验收报告、6 张真实 PNG、文档中的真实结果。

- [ ] **Step 1: 编写可重复验收脚本**

脚本参数与入口固定为：

```python
parser.add_argument("--content-id", type=int, default=20)
parser.add_argument("--expected-count", type=int, default=6)
parser.add_argument("--gotenberg-base-url", default="http://127.0.0.1:3000")
```

脚本执行：Gotenberg `/health`；`ImageTask.execute_for_content(..., content_id)`；读取该内容有效图片；用 Pillow 检查 2048×1152；检查 metadata 禁词、render key、模板版本、校验状态；检查输出目录无 `.part`；再次执行确认 `rendered_image_count == 0` 且 `reused_image_count == 6`。报告只写本地路径、尺寸、role、render key 前 12 位和校验结果，不写 API key 或外部响应。

实际任务入口使用现有生命周期依赖，但通过显式内容方法避免“最新内容”漂移：

```python
application = Application(PROJECT_ROOT)
application.initialize()
task = ImageTask(
    task_run_repository=application.task_run_repository,
    error_event_repository=application.error_event_repository,
)
metadata = task.execute_for_content(application._build_task_context(), content_id=args.content_id)
```

- [ ] **Step 2: 运行相关 Python 测试**

Run:

```powershell
python -m pytest tests/test_image_renderer_config.py tests/test_article_visual_spec.py tests/test_summary_visual_brief.py tests/test_summary_generation_orchestration.py tests/test_article_visual_template_service.py tests/test_gotenberg_screenshot_provider.py tests/test_article_visual_generation_service.py tests/test_media_preview_visual_spec.py tests/test_dynamic_project_image_count.py tests/test_seedream_provider.py -v
```

Expected: 全部 PASS。

- [ ] **Step 3: 运行真实六图与幂等验证**

Run:

```powershell
python scripts/verify_deterministic_article_visuals.py --content-id 20 --expected-count 6 --gotenberg-base-url http://127.0.0.1:3000
```

Expected:

- 报告 `status=passed`、`active_image_count=6`。
- 首轮实际渲染所有 render key 不匹配的图；第二轮 `rendered_image_count=0`、`reused_image_count=6`。
- 6 张图片都是 2048×1152 PNG，无 `.part`。
- metadata 和规格不含全局禁词。
- content 20 若是旧数据，6 项通过 legacy summary card 降级；不复用旧截断节点。

- [ ] **Step 4: 人眼查看 6 张真实图片**

Run:

```powershell
Get-Content outputs/visual-verification/content-20.json
```

逐张使用图片查看工具确认：中文无方框字、无裁切、无重叠、无重复节点、箭头方向与 metadata 一致、没有格式或图型元文字。把每张结论写入报告的 `visual_review`，取值只能是 `passed` 或包含明确问题的 `failed:<reason>`；任一 failed 都必须修复对应模板后重跑 Step 2–4。

- [ ] **Step 5: 验证失败保护**

Run: `python -m pytest tests/test_article_visual_generation_service.py::test_sixth_render_failure_produces_no_database_mutation -v`

Expected: PASS，旧资产保持有效，新资产创建方法未调用。

- [ ] **Step 6: 运行前端与全量测试**

Run:

```powershell
python -m pytest -q
npm test
npm run build
```

后两条命令 workdir 为 `web-ui`。Expected: 新增与相关测试全部通过；若全量 Python 测试仍存在工作区既有 interview library 失败，记录测试名和其与本功能无关的证据，不修改无关模块。

- [ ] **Step 7: 同步三份设计与运行文档**

`weekly_media_pipeline.md` 写入真实 content ID、图片数、尺寸、render/reuse 计数和失败保护结果；`media_creative_direction.md` 写入五种模板容量、禁词和 baoyu Type × Style × Palette 选择规则；`image_prompt_skill_research.md` 写入 baoyu 负责规划但不作为线上位图渲染器的边界。

- [ ] **Step 8: 提交真实验证与文档**

```bash
git add scripts/verify_deterministic_article_visuals.py docs/weekly_media_pipeline.md docs/media_creative_direction.md docs/image_prompt_skill_research.md
git commit -m "test: verify deterministic article visuals end to end"
```

---

## Final Acceptance Checklist

- [ ] `config.image.renderer.name` 为 `gotenberg_html`，首版概念背景关闭。
- [ ] SummaryTask 新输出全部包含通过校验的 `article_visual_spec_v1`。
- [ ] 旧内容缺少 visual spec 时只降级为 summary card，不复用旧截断标签和伪拓扑。
- [ ] 五种模板单元测试覆盖，所有文字 HTML 转义。
- [ ] Gotenberg 请求设置 `clip=true`、显式等待校验、资源失败和控制台异常失败。
- [ ] Gotenberg 与 Seedream Provider 均最多尝试 5 次。
- [ ] ImageTask 不调用 Seedream、GitHub 图片或 local card 主路径。
- [ ] 六图全部成功后才单事务创建新资产并替换旧资产。
- [ ] `render_key` 相同的第二次运行复用全部图片。
- [ ] 当前 6 项目样本生成 6 张真实 2048×1152 PNG，且无 `.part`。
- [ ] 图片中没有元文字、残缺中文、方框字、溢出、重叠、重复节点或错误箭头。
- [ ] PC 工作台展示图片总结方案、图型和校验状态，不伪装为 Seedream prompt。
- [ ] 相关 Python 测试、前端测试与构建通过；全量既有失败单独披露。
- [ ] 三份模块文档记录设计目标、取舍、调用链、依赖、验证和边界。
- [ ] 未执行任何生产部署。
