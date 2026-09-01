<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const emit = defineEmits(['authenticated'])

const loading = ref(true)
const submitting = ref(false)
const sendingLoginCode = ref(false)
const requiresBootstrap = ref(false)
const publicRegistrationEnabled = ref(true)
const cliBootstrapOnly = ref(false)
const mode = ref('login')
const loginMethod = ref('code')
const verificationStage = ref(false)
const challengeId = ref('')
const identity = ref('')
const email = ref('')
const displayName = ref('')
const password = ref('')
const confirmPassword = ref('')
const verificationCode = ref('')
const errorMessage = ref('')
const successMessage = ref('')
const requiresCliBootstrap = computed(() => requiresBootstrap.value && cliBootstrapOnly.value)

const careerCapabilities = [
  { label: '简历分析', number: '01', icon: 'resume' },
  { label: '真实岗位获取', number: '02', icon: 'job' },
  { label: '岗位匹配度分析', number: '03', icon: 'match' },
  { label: '智能一键打招呼', number: '04', icon: 'greeting' },
  { label: '实时面试助手', number: '05', icon: 'interview' }
]

const panelTitle = computed(() => {
  if (requiresCliBootstrap.value) return '请先在服务器初始化管理员'
  if (requiresBootstrap.value) return '创建管理员账户'
  if (mode.value === 'register') return verificationStage.value ? '验证邮箱并创建账户' : '创建平台账户'
  if (mode.value === 'reset') return verificationStage.value ? '设置新密码' : '找回登录密码'
  return '登录求职助手'
})

const panelDescription = computed(() => {
  if (requiresCliBootstrap.value) return '为了避免公网首个账户被抢先注册，管理员初始化只能由服务器拥有者在交互式终端完成。完成后刷新此页即可登录。'
  if (requiresBootstrap.value) return '首个账户将获得管理员权限。使用邮箱完成验证后，即可配置成员、角色与平台能力。'
  if (mode.value === 'register') return '注册后默认获得基础访问权限。高级管理功能需由管理员单独授权。'
  if (mode.value === 'reset') return '验证码仅发送到已验证邮箱，用于确认本次密码重置操作。'
  return '继续你的求职进度。'
})

const primaryLabel = computed(() => {
  if (requiresBootstrap.value) return verificationStage.value ? '验证并进入平台' : '发送管理员验证码'
  if (mode.value === 'register') return verificationStage.value ? '验证并创建账户' : '发送注册验证码'
  if (mode.value === 'reset') return verificationStage.value ? '保存新密码' : '发送重置验证码'
  if (loginMethod.value === 'code') return '登录'
  return '登录'
})

const visibleEmail = computed(() => email.value.trim())
const isCodeStage = computed(() => verificationStage.value && !requiresCliBootstrap.value)
const flowStep = computed(() => (isCodeStage.value ? 2 : 1))
const maskedEmail = computed(() => {
  const value = visibleEmail.value
  const atIndex = value.indexOf('@')
  if (atIndex < 2) return value
  return `${value.slice(0, 2)}${'•'.repeat(Math.min(6, Math.max(2, atIndex - 2)))}${value.slice(atIndex)}`
})

onMounted(() => {
  document.body.classList.add('auth-page-active')
  window.addEventListener('popstate', syncModeFromLocation)
  syncModeFromLocation()
  void loadBootstrapStatus()
})

onBeforeUnmount(() => {
  document.body.classList.remove('auth-page-active')
  window.removeEventListener('popstate', syncModeFromLocation)
})

async function loadBootstrapStatus() {
  loading.value = true
  clearMessages()
  try {
    const response = await fetch('/api/auth/bootstrap-status', {
      credentials: 'include',
      cache: 'no-store'
    })
    if (!response.ok) throw new Error(await responseError(response, '无法读取账户服务状态'))
    const payload = await response.json()
    requiresBootstrap.value = Boolean(payload.requires_bootstrap)
    publicRegistrationEnabled.value = payload.public_registration_enabled !== false
    cliBootstrapOnly.value = Boolean(payload.cli_bootstrap_only)
    if (requiresBootstrap.value) {
      mode.value = 'register'
      replaceModeInLocation('register')
    }
    if (!requiresBootstrap.value && !publicRegistrationEnabled.value && mode.value === 'register') {
      switchMode('login', { replace: true })
    }
  } catch (error) {
    errorMessage.value = readableError(error, '账户服务暂不可用。请确认后端服务和 PostgreSQL 已启动。')
  } finally {
    loading.value = false
  }
}

function syncModeFromLocation() {
  if (window.location.pathname === '/register') {
    mode.value = 'register'
    return
  }
  if (window.location.pathname === '/forgot-password') {
    mode.value = 'reset'
    return
  }
  const requestedMode = new URLSearchParams(window.location.search).get('auth')
  if (requestedMode === 'register' || requestedMode === 'reset') {
    mode.value = requestedMode
    return
  }
  mode.value = 'login'
}

function replaceModeInLocation(nextMode) {
  const nextUrl = new URL(window.location.href)
  nextUrl.pathname = authPathForMode(nextMode)
  nextUrl.searchParams.delete('auth')
  window.history.replaceState({}, '', `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`)
}

function switchMode(nextMode, { replace = false } = {}) {
  if (nextMode === 'register' && !publicRegistrationEnabled.value && !requiresBootstrap.value) return
  mode.value = nextMode
  verificationStage.value = false
  challengeId.value = ''
  verificationCode.value = ''
  password.value = ''
  confirmPassword.value = ''
  clearMessages()
  const nextUrl = new URL(window.location.href)
  nextUrl.pathname = authPathForMode(nextMode)
  nextUrl.searchParams.delete('auth')
  window.history[replace ? 'replaceState' : 'pushState']({}, '', `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`)
}

function switchLoginMethod(nextMethod) {
  if (nextMethod === loginMethod.value || submitting.value) return
  loginMethod.value = nextMethod
  verificationStage.value = false
  challengeId.value = ''
  verificationCode.value = ''
  password.value = ''
  clearMessages()
}

function authPathForMode(nextMode) {
  if (nextMode === 'register') return '/register'
  if (nextMode === 'reset') return '/forgot-password'
  return '/login'
}

function clearMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

function validatePassword() {
  if (password.value.length < 8) {
    errorMessage.value = '密码至少需要 8 位。'
    return false
  }
  if (mode.value !== 'login' && password.value !== confirmPassword.value) {
    errorMessage.value = '两次输入的密码不一致。'
    return false
  }
  return true
}

function validateRegistrationFields() {
  if (!email.value.trim() || !displayName.value.trim()) {
    errorMessage.value = '请填写邮箱和显示名称。'
    return false
  }
  if (!validateEmail(email.value)) {
    errorMessage.value = '请输入有效的邮箱地址。'
    return false
  }
  return validatePassword()
}

function validateVerificationCode() {
  if (!/^\d{6}$/.test(verificationCode.value.trim())) {
    errorMessage.value = '请输入邮件中的 6 位数字验证码。'
    return false
  }
  return true
}

async function submit() {
  if (submitting.value) return
  clearMessages()

  if (requiresBootstrap.value || mode.value === 'register') {
    await submitRegistration()
    return
  }
  if (mode.value === 'reset') {
    await submitReset()
    return
  }
  if (loginMethod.value === 'code') {
    await submitEmailCodeLogin()
    return
  }
  await submitLogin()
}

async function submitEmailCodeLogin() {
  if (!validateEmail(email.value)) {
    errorMessage.value = '请输入有效的注册邮箱。'
    return
  }
  if (!challengeId.value) {
    errorMessage.value = '请先获取登录验证码。'
    return
  }
  if (!validateVerificationCode()) return

  submitting.value = true
  try {
    const response = await fetch('/api/auth/email-login/verify', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ challenge_id: challengeId.value, code: verificationCode.value.trim() })
    })
    if (!response.ok) throw new Error(await responseError(response, '验证码登录失败'))
    const payload = await response.json()
    emit('authenticated', payload.user)
  } catch (error) {
    errorMessage.value = readableError(error, '验证码登录失败，请稍后再试。')
  } finally {
    submitting.value = false
  }
}

async function sendEmailLoginCode() {
  clearMessages()
  if (!validateEmail(email.value)) {
    errorMessage.value = '请输入有效的注册邮箱。'
    return
  }

  sendingLoginCode.value = true
  try {
    const response = await fetch('/api/auth/email-login/send-code', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.value.trim() })
    })
    if (!response.ok) throw new Error(await responseError(response, '获取验证码失败'))
    const payload = await response.json()
    challengeId.value = payload.challenge_id
    successMessage.value = '验证码已发送，请在 10 分钟内完成登录。'
  } catch (error) {
    errorMessage.value = readableError(error, '获取验证码失败，请稍后再试。')
  } finally {
    sendingLoginCode.value = false
  }
}

async function submitRegistration() {
  if (!verificationStage.value && !validateRegistrationFields()) return
  if (verificationStage.value && !validateVerificationCode()) return

  submitting.value = true
  try {
    const prefix = requiresBootstrap.value ? '/api/auth/bootstrap' : '/api/auth/register'
    const response = await fetch(
      verificationStage.value ? `${prefix}/verify` : `${prefix}/send-code`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          verificationStage.value
            ? { challenge_id: challengeId.value, code: verificationCode.value.trim() }
            : { email: email.value.trim(), display_name: displayName.value.trim(), password: password.value }
        )
      }
    )
    if (!response.ok) throw new Error(await responseError(response, '账户验证失败'))
    const payload = await response.json()
    if (!verificationStage.value) {
      challengeId.value = payload.challenge_id
      verificationStage.value = true
      successMessage.value = '验证码已发送，请在有效期内完成验证。'
    } else {
      emit('authenticated', payload.user)
    }
  } catch (error) {
    errorMessage.value = readableError(error, '账户验证失败，请稍后再试。')
  } finally {
    submitting.value = false
  }
}

async function submitReset() {
  if (!email.value.trim()) {
    errorMessage.value = '请输入已验证的登录邮箱。'
    return
  }
  if (!verificationStage.value && !validateEmail(email.value)) {
    errorMessage.value = '请输入有效的邮箱地址。'
    return
  }
  if (verificationStage.value && (!validateVerificationCode() || !validatePassword())) return

  submitting.value = true
  try {
    const response = await fetch(
      verificationStage.value ? '/api/auth/password-reset/verify' : '/api/auth/password-reset/send-code',
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          verificationStage.value
            ? { challenge_id: challengeId.value, code: verificationCode.value.trim(), new_password: password.value }
            : { email: email.value.trim() }
        )
      }
    )
    if (!response.ok) throw new Error(await responseError(response, '密码重置失败'))
    const payload = await response.json()
    if (!verificationStage.value) {
      // 后端为未注册邮箱返回泛化结果，不显示不存在账号的提示。
      // 此时没有 challenge，前端不能进入验证码提交阶段。
      if (!payload.challenge_id) {
        successMessage.value = '若该邮箱已完成绑定，验证码会在几分钟内送达。请检查收件箱和垃圾邮件；未收到可稍后重试。'
        return
      }
      challengeId.value = payload.challenge_id
      verificationStage.value = true
      successMessage.value = '验证码已发送，请输入验证码并设置新密码。'
    } else {
      successMessage.value = '密码已更新，请使用新密码登录。'
      verificationStage.value = false
      mode.value = 'login'
      replaceModeInLocation('login')
      password.value = ''
      confirmPassword.value = ''
      verificationCode.value = ''
    }
  } catch (error) {
    errorMessage.value = readableError(error, '密码重置失败，请稍后再试。')
  } finally {
    submitting.value = false
  }
}

function returnToEntry() {
  verificationStage.value = false
  challengeId.value = ''
  verificationCode.value = ''
  password.value = ''
  confirmPassword.value = ''
  clearMessages()
}

async function submitLogin() {
  if (!identity.value.trim() || !password.value) {
    errorMessage.value = '请输入邮箱（或历史用户名）和密码。'
    return
  }
  submitting.value = true
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identity: identity.value.trim(), password: password.value })
    })
    if (!response.ok) throw new Error(await responseError(response, '登录失败'))
    const payload = await response.json()
    emit('authenticated', payload.user)
  } catch (error) {
    errorMessage.value = readableError(error, '登录失败，请检查账号和密码。')
  } finally {
    submitting.value = false
  }
}

function validateEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json()
    return payload?.detail || fallback
  } catch {
    return fallback
  }
}

function readableError(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback
}
</script>

<template>
  <main class="login-page">
    <section class="login-career-panel" aria-label="求职助手能力介绍">
      <div class="login-career-grid" aria-hidden="true"></div>

      <div class="login-career-brand">
        <div class="login-brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none"><path d="M12 3v4m0 10v4M3 12h4m10 0h4m-4.2-4.8 2-2m-13.6 13.6 2-2m0-9.6-2-2m13.6 13.6-2-2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="m9.2 12 1.8 1.8 4-4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </div>
        <span>求职助手</span>
      </div>

      <div class="login-career-stage">
        <div class="login-career-copy">
          <p class="login-career-label">求职 Agent 为你持续推进</p>
          <h1>从一份简历，<br>到拿下<em>更合适的机会</em></h1>
          <p>求职 Agent 自动读懂简历、持续获取真实岗位、解释匹配度，并协助沟通与面试。</p>
        </div>

        <div class="login-agent-rail" aria-label="求职 Agent 执行流程">
          <div class="login-agent-track" aria-hidden="true">
            <span class="login-agent-signal"></span>
            <span class="login-agent-orbit"></span>
          </div>
          <ol class="login-agent-stations">
            <li v-for="item in careerCapabilities" :key="item.label" class="login-agent-station">
              <span class="login-agent-node" aria-hidden="true">
                <svg v-if="item.icon === 'resume'" viewBox="0 0 24 24" fill="none"><path d="M7 3h7l4 4v14H7zM14 3v5h5M10 12h5m-5 4h3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
                <svg v-else-if="item.icon === 'job'" viewBox="0 0 24 24" fill="none"><path d="M4 8h16v11H4zM9 8V5h6v3m-11 4c5 2 11 2 16 0" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
                <svg v-else-if="item.icon === 'match'" viewBox="0 0 24 24" fill="none"><path d="M8 4H5a1 1 0 0 0-1 1v3m12-4h3a1 1 0 0 1 1 1v3M8 20H5a1 1 0 0 1-1-1v-3m12 4h3a1 1 0 0 0 1-1v-3m-11-4 2 2 4-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
                <svg v-else-if="item.icon === 'greeting'" viewBox="0 0 24 24" fill="none"><path d="M4 5h16v12H9l-5 4zM8 9h8m-8 4h5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
                <svg v-else viewBox="0 0 24 24" fill="none"><path d="M4 13h2l2-6 3 11 3-12 2 7h4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </span>
              <span class="login-agent-number">{{ item.number }}</span>
              <strong>{{ item.label }}</strong>
            </li>
          </ol>
        </div>
      </div>
    </section>

    <section class="login-form-panel">
      <div class="login-form-wrap">
        <div class="login-mobile-brand" aria-label="求职助手">
          <div class="login-brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none"><path d="M12 3v4m0 10v4M3 12h4m10 0h4m-4.2-4.8 2-2m-13.6 13.6 2-2m0-9.6-2-2m13.6 13.6-2-2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="m9.2 12 1.8 1.8 4-4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </div>
          <span>求职助手</span>
        </div>

        <div class="login-form-heading" :class="{ 'is-code-stage': isCodeStage }">
          <h2>{{ panelTitle }}</h2>
          <p>{{ panelDescription }}</p>
        </div>

        <div v-if="loading" class="login-loading" role="status">
          <span class="login-loading-dot" aria-hidden="true"></span>
          正在连接账户服务…
        </div>
        <template v-else>
          <section v-if="requiresCliBootstrap" class="login-cli-bootstrap" aria-live="polite">
            <strong>管理员尚未初始化</strong>
            <p>请登录部署服务器后，在项目目录执行：</p>
            <code>docker compose --env-file .env.production -f docker-compose.production.yml exec -it career-api python scripts/bootstrap_first_admin.py</code>
            <p class="login-hint">命令会在终端中安全读取邮箱、显示名称和密码；请勿添加 <code>-T</code>，也不要把密码写入命令行。</p>
          </section>

          <template v-else>
            <div
              v-if="mode === 'login'"
              class="login-method-tabs"
              role="tablist"
              aria-label="选择登录方式"
            >
              <button
                type="button"
                role="tab"
                :aria-selected="loginMethod === 'code'"
                :class="{ active: loginMethod === 'code' }"
                @click="switchLoginMethod('code')"
              >
                验证码登录
              </button>
              <button
                type="button"
                role="tab"
                :aria-selected="loginMethod === 'password'"
                :class="{ active: loginMethod === 'password' }"
                @click="switchLoginMethod('password')"
              >
                密码登录
              </button>
            </div>

            <section v-if="isCodeStage" class="verification-flow" aria-live="polite">
              <div class="verification-flow-mark" aria-hidden="true">{{ flowStep }}</div>
              <div>
                <strong>验证码已发送</strong>
                <p>请查收 <b>{{ maskedEmail }}</b>，输入验证码继续。</p>
              </div>
            </section>

            <form class="login-form" @submit.prevent="submit">
              <template v-if="mode === 'login' && !requiresBootstrap">
                <template v-if="loginMethod === 'password'">
                  <label for="login-identity">
                    <span>邮箱</span>
                    <input id="login-identity" v-model.trim="identity" autocomplete="username" maxlength="160" placeholder="name@example.com" autofocus />
                  </label>
                  <div class="login-field">
                    <div class="login-label-row">
                      <label for="login-password">密码</label>
                      <button type="button" @click="switchMode('reset')">忘记密码？</button>
                    </div>
                    <input id="login-password" v-model="password" type="password" autocomplete="current-password" placeholder="输入密码" />
                  </div>
                </template>
                <template v-else>
                  <label for="login-email">
                    <span>邮箱</span>
                    <input id="login-email" v-model.trim="email" type="email" autocomplete="email" maxlength="254" placeholder="name@example.com" autofocus />
                  </label>
                  <div class="login-field">
                    <label for="login-code">6 位验证码</label>
                    <div class="login-code-row">
                      <input id="login-code" class="verification-code-input" v-model.trim="verificationCode" autocomplete="one-time-code" inputmode="numeric" maxlength="6" placeholder="000000" />
                      <button type="button" class="login-code-send" :disabled="sendingLoginCode || submitting" @click="sendEmailLoginCode">
                        {{ sendingLoginCode ? '发送中…' : challengeId ? '重新获取' : '获取验证码' }}
                      </button>
                    </div>
                    <small class="login-field-hint">验证码将发送到该邮箱，10 分钟内有效。</small>
                  </div>
                </template>
              </template>

              <template v-else-if="(mode === 'register' || requiresBootstrap) && !verificationStage">
                <label>
                  <span>常用邮箱</span>
                  <input v-model.trim="email" type="email" autocomplete="email" maxlength="254" placeholder="name@example.com" autofocus />
                </label>
                <label>
                  <span>显示名称</span>
                  <input v-model.trim="displayName" autocomplete="name" maxlength="120" placeholder="例如：兴兴" />
                </label>
                <div class="login-password-grid">
                  <label><span>设置密码 <small>至少 8 位</small></span><input v-model="password" type="password" autocomplete="new-password" minlength="8" placeholder="至少 8 位" /></label>
                  <label><span>确认密码</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="8" placeholder="再次输入" /></label>
                </div>
              </template>

              <template v-else-if="mode === 'reset' && !verificationStage">
                <label>
                  <span>已绑定邮箱</span>
                  <input v-model.trim="email" type="email" autocomplete="email" maxlength="254" placeholder="name@example.com" autofocus />
                  <small class="login-field-hint">系统会向该邮箱发送一次性验证码，用于确认重置密码。</small>
                </label>
              </template>

              <template v-else>
                <label>
                  <span>6 位验证码</span>
                  <input class="verification-code-input" v-model.trim="verificationCode" autocomplete="one-time-code" inputmode="numeric" maxlength="6" placeholder="000000" autofocus />
                </label>
                <template v-if="mode === 'reset'">
                  <div class="login-password-grid">
                    <label><span>新密码 <small>至少 8 位</small></span><input v-model="password" type="password" autocomplete="new-password" minlength="8" placeholder="设置新密码" /></label>
                    <label><span>确认新密码</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="8" placeholder="再次输入" /></label>
                  </div>
                </template>
              </template>

              <p v-if="successMessage" class="login-success" role="status">{{ successMessage }}</p>
              <p v-if="errorMessage" class="login-error" role="alert">{{ errorMessage }}</p>
              <button class="login-submit" type="submit" :disabled="submitting">{{ submitting ? '正在处理中…' : primaryLabel }}</button>
            </form>

            <div v-if="!requiresBootstrap && mode === 'login' && publicRegistrationEnabled" class="login-register-entry">
              <span>没有账号？</span>
              <button type="button" @click="switchMode('register')">创建账号</button>
            </div>

            <div class="login-switches">
              <template v-if="!requiresBootstrap && mode === 'register' && !verificationStage">
                <span>已有账号？</span>
                <button type="button" @click="switchMode('login')">登录</button>
              </template>
              <template v-else-if="!requiresBootstrap && mode === 'reset' && !verificationStage">
                <span>想起密码了？</span>
                <button type="button" @click="switchMode('login')">返回登录</button>
              </template>
              <button v-else-if="verificationStage" type="button" @click="returnToEntry">修改邮箱或重新发送</button>
            </div>
          </template>
        </template>

        <p v-if="requiresCliBootstrap" class="login-footer">管理员完成服务器端初始化后，刷新本页并使用初始化邮箱登录。</p>
        <p v-else-if="mode !== 'login'" class="login-footer">验证码有效期为 10 分钟；注册后即绑定该邮箱，后续可使用验证码找回密码。</p>
      </div>
    </section>
  </main>
</template>
