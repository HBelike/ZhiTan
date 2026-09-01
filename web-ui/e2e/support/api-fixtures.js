const conversation = {
  id: 'conversation-1',
  title: 'Agent 工程师求职准备',
  updated_at: '2026-08-27T10:00:00+08:00'
}

const modelProfile = {
  readiness: 'ready',
  profile: {
    id: 'model-1',
    profile_key: 'preview-model',
    display_name: '验收模型',
    model_id: 'preview-model',
    provider_key: 'preview',
    capabilities: ['text'],
    cost_tier: 'free'
  }
}

const conversationContext = {
  candidate_profile: { id: 'candidate-1', version: 1, display_name: '基础简历' },
  target_role: {
    id: 'role-1',
    version: 1,
    company_name: '示例科技',
    role_name: 'Agent 工程师',
    job_description_text: '负责 Agent 应用开发'
  },
  assessment: null
}

const resumeAnalysis = {
  original_markdown: '# 张三\n\nAgent 工程师，具有完整项目交付经验。',
  job_description_text: '负责 Agent 应用开发与质量保障。',
  job_title: 'Agent 工程师',
  model_profile_id: 'model-1',
  analysis_summary: '经历方向匹配，建议强化项目结果和协作边界。',
  suggestions: [
    {
      id: 'suggestion-1',
      title: '强化 Agent 项目结果',
      priority: '高',
      rationale: '目标岗位强调工程交付。',
      original_evidence: '具有完整项目交付经验。',
      job_evidence: '负责 Agent 应用开发与质量保障。',
      proposed_change: '补充项目职责、技术取舍和可验证结果。'
    }
  ]
}

export async function installApiFixtures(page) {
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/auth/bootstrap-status') {
      await route.fulfill({ json: { requires_bootstrap: false, public_registration_enabled: true } })
      return
    }
    if (path === '/api/career/model-profiles') {
      await route.fulfill({ json: { items: [modelProfile] } })
      return
    }
    if (path === '/api/career/candidate-profiles') {
      await route.fulfill({ json: { items: [conversationContext.candidate_profile] } })
      return
    }
    if (path === '/api/career/free-model-catalog') {
      await route.fulfill({ json: { items: [] } })
      return
    }
    if (path === '/api/career/conversations' && request.method() === 'GET') {
      await route.fulfill({ json: { items: [conversation], page: 1, page_size: 5, total: 1, total_pages: 1 } })
      return
    }
    if (path === `/api/career/conversations/${conversation.id}`) {
      await route.fulfill({
        json: {
          conversation,
          context: conversationContext,
          messages: [],
          latest_turn: null,
          last_model_selection: null,
          turn_limit: { current_turn: 0, maximum_turns: 30 }
        }
      })
      return
    }
    if (path === `/api/career/conversations/${conversation.id}/active-turns`) {
      await route.fulfill({ json: { items: [] } })
      return
    }
    if (path === `/api/career/conversations/${conversation.id}/context-usage`) {
      await route.fulfill({ json: { context_usage: null, turn_limit: { current_turn: 0, maximum_turns: 30 } } })
      return
    }
    if (path === '/api/career/resume-optimizations' && request.method() === 'GET') {
      await route.fulfill({ json: { items: [], total: 0, page: 1 } })
      return
    }
    if (path === '/api/career/resume-optimizations/analyze' && request.method() === 'POST') {
      await route.fulfill({ json: resumeAnalysis })
      return
    }

    await route.abort('blockedbyclient')
  })
}
