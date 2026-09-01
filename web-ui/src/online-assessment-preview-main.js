import { createApp } from 'vue'

import OnlineAssessmentAssistantPage from './components/OnlineAssessmentAssistantPage.vue'
import { ASSESSMENT_SESSION_KEY } from './online-assessment/state.js'

const scenario = new URLSearchParams(window.location.search).get('scenario')

if (scenario === 'empty') {
  window.sessionStorage.removeItem(ASSESSMENT_SESSION_KEY)
}

if (scenario === 'workbench') {
  window.sessionStorage.setItem(ASSESSMENT_SESSION_KEY, JSON.stringify({
    runVersion: 5,
    phase: 'ready',
    captureToken: 'preview-token',
    sourceTabId: 23,
    problem: {
      problem_id: '867b88bd-c73a-41c7-97ff-238be03596fc',
      source_url: 'https://assessment.example.com/problem/two-sum',
      source_platform: 'generic',
      title: '两数之和',
      statement: '给定一个整数数组 nums 和一个整数目标值 target，请在数组中找出和为目标值的两个整数，并返回它们的数组下标。每种输入只会对应一个答案，且同一个元素不能重复出现。',
      constraints: ['2 ≤ nums.length ≤ 10⁴', '-10⁹ ≤ nums[i], target ≤ 10⁹'],
      examples: [{
        test_id: 'public-1',
        kind: 'public',
        input_payload: [[2, 7, 11, 15], 9],
        expected_output: '[0,1]',
        explanation: ''
      }],
      starter_code: 'class Solution:\n    def twoSum(self, nums, target):',
      language: 'python',
      interface_kind: 'function',
      function_signature: 'def twoSum(nums: list[int], target: int) -> list[int]',
      confidence: 0.94,
      incomplete_reasons: []
    },
    solution: {
      approach_markdown: '遍历数组，用哈希表保存已经见过的数字及其下标。每次检查 target - nums[i] 是否已经出现。',
      code: 'class Solution:\n    def twoSum(self, nums, target):\n        seen = {}\n        for index, value in enumerate(nums):\n            needed = target - value\n            if needed in seen:\n                return [seen[needed], index]\n            seen[value] = index',
      language: 'python',
      time_complexity: 'O(n)',
      space_complexity: 'O(n)',
      assumptions: []
    },
    tests: [
      { test_id: 'public-1', kind: 'public', input_payload: [[2, 7, 11, 15], 9], expected_output: '[0,1]', explanation: '' },
      { test_id: 'generated-1', kind: 'generated', input_payload: [[3, 3], 6], expected_output: '[0,1]', explanation: '覆盖重复元素组成答案的情况。' },
      { test_id: 'generated-2', kind: 'generated', input_payload: [[-3, 4, 3, 90], 0], expected_output: '[0,2]', explanation: '覆盖负数与零目标值。' }
    ],
    report: {
      compile_status: 'skipped',
      tests: [
        { test_id: 'public-1', kind: 'public', status: 'passed', actual_output: '[0,1]', error_summary: '', duration_ms: 32 },
        { test_id: 'generated-1', kind: 'generated', status: 'passed', actual_output: '[0,1]', error_summary: '', duration_ms: 27 },
        { test_id: 'generated-2', kind: 'generated', status: 'passed', actual_output: '[0,2]', error_summary: '', duration_ms: 30 }
      ],
      passed_count: 3,
      failed_count: 0,
      duration_ms: 89,
      final_status: 'passed',
      repair_rounds: 0
    },
    items: []
  }))
}

createApp(OnlineAssessmentAssistantPage).mount('#app')
