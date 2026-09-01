"""验证简历结构化归纳不依赖 LLM，且能识别中英文常见标题。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.resume_normalizer import ResumeNormalizer, ResumeSection


def main() -> None:
    """验证中文 Markdown、英文标题和未归类文本的可解释输出。"""

    normalizer = ResumeNormalizer()
    chinese_profile = normalizer.normalize(
        """
# 个人总结
三年后端开发经验，期望参与 AI Agent 工程化项目。

# 工作经历
2024.03 - 至今 某科技公司，后端工程师
- 负责工作流服务的稳定性建设。

## 项目经历
智能内容生产平台：负责 LangGraph 编排与模型网关。

# 教育经历
某大学，计算机科学与技术。

# 专业技能
Python、FastAPI、PostgreSQL、Docker。
""".strip(),
    )
    assert chinese_profile.has_section(ResumeSection.PROFILE)
    assert chinese_profile.has_section(ResumeSection.WORK_EXPERIENCE)
    assert chinese_profile.has_section(ResumeSection.PROJECTS)
    assert chinese_profile.has_section(ResumeSection.EDUCATION)
    assert chinese_profile.has_section(ResumeSection.SKILLS)
    assert "LangGraph 编排" in chinese_profile.to_model_outline()
    assert "【项目经历】" in chinese_profile.to_model_outline()
    assert chinese_profile.unclassified_text == ""

    english_profile = normalizer.normalize(
        """
PROFESSIONAL SUMMARY
Backend engineer focused on reliable agent workflows.

WORK EXPERIENCE
Built content review services.

PROJECTS
Implemented a document-understanding pipeline.

Unlabeled note for later confirmation.
""".strip(),
    )
    assert english_profile.has_section(ResumeSection.PROFILE)
    assert english_profile.has_section(ResumeSection.WORK_EXPERIENCE)
    assert english_profile.has_section(ResumeSection.PROJECTS)
    assert not english_profile.has_section(ResumeSection.EDUCATION)
    assert "Unlabeled note" in english_profile.to_model_outline()

    empty_profile = normalizer.normalize("  \n")
    assert empty_profile.sections == ()
    assert empty_profile.source_character_count == 0
    print("career_resume_normalizer_ok")


if __name__ == "__main__":
    main()
