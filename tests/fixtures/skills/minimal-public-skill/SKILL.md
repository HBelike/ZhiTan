---
name: minimal-public-skill
description: Repository-owned fixture for testing the ZhiTan Skill runtime.
---

# Minimal Public Skill

Read the user's request and gather only the evidence needed for the answer.

Use the task text from `$ARGUMENTS` and resolve bundled resources relative to
`${SKILL_DIR}`. Preserve factual uncertainty and return a concise result.
