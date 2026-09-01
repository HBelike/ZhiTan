# 微信公众号文章标题处理

## 设计目标

- 审核预览展示生成内容中保存的完整标题，长标题在页面内自然换行。
- 创建微信公众号草稿时不再为了版面观感主动缩短标题。
- 超过微信接口限制的标题明确报错，避免静默截断后改变原意。

## 技术取舍

微信 `draft/add` 官方文档规定 `title` 总长度不超过 32 个字。本项目以 32 个
Python 字符作为提交边界：标题在提交前只归一化空白，不添加省略号；超过边界时
抛出包含实际长度的 `ValueError`。没有引入字节截断或自动摘要逻辑。

## 调用链与依赖

`SummaryTask` 要求模型生成不超过 32 字的标题，并在全局内容合同中执行同一校验；
校验失败会进入既有的全局字段重试。通过后保存完整 `content.title` →
`MediaPreviewService` 返回完整 `wechat_title` → 审核页优先渲染
`article_layout.title` 或 `content.title`。
创建草稿时，`WechatDraftCreationService` 调用 `validate_wechat_title`，通过后原样
写入 `/cgi-bin/draft/add` 请求。

外部合同依据：
<https://developers.weixin.qq.com/doc/subscription/api/draftbox/draftmanage/api_draft_add.html>

## 验证与边界

- 32 字标题可以提交，33 字标题会在请求前失败。
- 审核页不再使用旧的 28 字紧凑标题逻辑。
- 生成提示词和服务端合同使用同一个 32 字边界，避免到发布环节才发现超限。
