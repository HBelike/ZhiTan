# Third-Party Notices

ZhiTan original code is distributed under the Apache License 2.0. Third-party materials keep their own licenses and are not relicensed by the ZhiTan license.

## Bundled font

`assets/fonts/NotoSansSC-VF.ttf` is part of the Noto CJK font family and is distributed under the SIL Open Font License 1.1. The complete license text is included at `assets/fonts/Noto-CJK-LICENSE.txt`.

## Reviewed and attributed implementations

The online-assessment module was implemented in this repository after reviewing the following projects. The detailed scope and pinned revisions are recorded in [`docs/open_source_attribution.md`](docs/open_source_attribution.md).

| Project | License | Use in ZhiTan |
|---|---|---|
| [RaheesAhmed/LeetCode-AI-Assistant](https://github.com/RaheesAhmed/LeetCode-AI-Assistant) | MIT | Selector and extraction design reference |
| [harry-the-nerd/open-interview-assistant](https://github.com/harry-the-nerd/open-interview-assistant) | Apache-2.0 | User-gesture permissions and fallback design reference |
| [LeetBuddyAI/LeetBuddy](https://github.com/LeetBuddyAI/LeetBuddy) | MIT | Single-problem preparation flow reference |
| [zubyj/leetcode-explained](https://github.com/zubyj/leetcode-explained) | MIT | Manifest V3 organization reference |
| [engineer-man/piston](https://github.com/engineer-man/piston) | MIT | Independently deployed code-execution container |

Where a source file contains a more specific attribution header, that header controls and must be preserved. Reviewing an idea does not imply that an upstream project endorses ZhiTan.

## Dependencies and container images

Python packages, npm packages, GitHub Actions, and container images listed in dependency manifests and Compose files are governed by their upstream licenses and terms. Deployers and redistributors are responsible for reviewing the licenses of the exact versions they use. This notice is not a substitute for those upstream license texts.
