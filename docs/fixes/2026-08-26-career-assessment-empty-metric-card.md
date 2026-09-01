# 求职助手空指标卡修复记录

## 设计目标

基础规则降级分析产生不可计算维度时，不在职业匹配度区域展示“待分析”指标卡；有效的 `0%` 分数必须正常展示。

## 原因与技术取舍

`lexical-evidence-v2` 会保留四个固定维度。某一维度没有可计分要求时，后端使用 `status: insufficient_data` 和 `score: null` 表达“不可计算”。前端动态 Judge 结果已经过滤这类维度，但旧格式分支只检查维度 key 是否存在，因而把空分数渲染成“待分析”。

本次只统一前端卡片过滤规则，不修改后端结果结构或历史持久化数据。过滤条件使用 `status === 'ready' && score !== null`，因此 `score: 0` 仍是有效分数。

## 调用链与依赖

`context.assessment` → `buildAssessmentCards()` → `CareerContextRail` → `CareerJobCanvas`。修复位于 `buildAssessmentCards()` 的旧格式分支，不新增依赖。

## 验证结果

- 增加基础规则降级回归用例，验证 `insufficient_data/null` 不生成卡片。
- 同一用例验证 `critical_gap/0` 继续生成卡片并保留 `0` 分数。
- `npm test` 通过，共 128 项测试，无失败或跳过。
- `npm run build` 通过；现有大 chunk 提示不影响本次构建结果。

## 后续边界

后端继续保留不可计算维度，供诊断和兼容用途；本次不改变 Judge、基础规则算法、评分公式或重新分析流程。
