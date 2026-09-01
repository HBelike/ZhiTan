import test from 'node:test'
import assert from 'node:assert/strict'
import { parseJobCanvasText } from './job-canvas-parser.js'

test('按明确标题解析职责和要求，不把公司介绍当作职责', () => {
  const jobText = `# AI全栈工程师

公司：食物主义
薪资：18-25K
地点：上海

## 职位描述
岗位情况：万店引力 AI Lab，隶属于食物主义集团。

核心职责：
1、开发并优化核心 AI 模型与 Agent 系统，提升生成质量、准确性与效率。
将提示词工程深度融入代码，构建针对实际场景的先进算法解决方案。
2、设计高可用、可扩展的核心后端，管理 API 调用、数据流水线及微服务架构。
负责 DevOps 与安全，管理 Docker/K8s 部署并优化系统可靠性。
3. 构建 AI 原生创作平台的前端架构，兼顾性能与跨平台兼容性。
与设计团队协作，将复杂能力转化为直观的用户体验。

职位要求：
1、同时精通前端 React/Vue 和后端 Python、Go 或 Node.js。
2、拥有 LLM、Agent 系统以及模型部署的实战经验。

加分项：
具备面向海外市场打造产品的经验。

## 技能关键词
day、AI+、Lead、Python、MCP、React、SMC`

  const parsed = parseJobCanvasText(jobText, {
    companyName: '食物主义',
    roleName: 'AI全栈工程师',
    requirements: [
      { skills: ['React', 'Vue', 'Python', 'Go', 'Node.js'] },
      { skills: ['LLM', 'MCP'] }
    ]
  })

  assert.equal(parsed.hasReliableStructure, true)
  assert.deepEqual(parsed.sections.responsibilities, [
    '开发并优化核心 AI 模型与 Agent 系统，提升生成质量、准确性与效率。\n将提示词工程深度融入代码，构建针对实际场景的先进算法解决方案。',
    '设计高可用、可扩展的核心后端，管理 API 调用、数据流水线及微服务架构。\n负责 DevOps 与安全，管理 Docker/K8s 部署并优化系统可靠性。',
    '构建 AI 原生创作平台的前端架构，兼顾性能与跨平台兼容性。\n与设计团队协作，将复杂能力转化为直观的用户体验。'
  ])
  assert.equal(parsed.sections.requirements.length, 2)
  assert.deepEqual(parsed.skills, ['React', 'Vue', 'Python', 'Go', 'Node.js', 'LLM', 'MCP'])
  assert.equal(parsed.skills.includes('day'), false)
  assert.equal(parsed.skills.includes('AI+'), false)
  assert.equal(parsed.sections.responsibilities.some((item) => item.includes('万店引力')), false)
  assert.equal(parsed.other.some((item) => item.includes('公司：')), false)
})

test('没有可靠职责与要求分区时返回职位描述原文作为降级展示', () => {
  const jobText = `# 后端工程师

公司：示例公司

## 职位描述
参与业务系统建设，根据实际项目安排承担研发工作。
熟悉常见服务端开发流程。`

  const parsed = parseJobCanvasText(jobText, {
    companyName: '示例公司',
    roleName: '后端工程师',
    requirements: []
  })

  assert.equal(parsed.hasReliableStructure, false)
  assert.equal(parsed.rawJobDescription, '参与业务系统建设，根据实际项目安排承担研发工作。\n熟悉常见服务端开发流程。')
  assert.deepEqual(parsed.sections.responsibilities, [])
  assert.deepEqual(parsed.sections.requirements, [])
})
