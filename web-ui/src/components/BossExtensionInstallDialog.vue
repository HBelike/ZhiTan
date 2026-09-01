<script setup>
import {
  BOSS_EXTENSION_DOWNLOAD_URL,
  bossExtensionGuideUrl
} from '../boss-extension-onboarding.js'

const appHost = globalThis.location?.host || 'ZhiTan'

defineProps({
  open: { type: Boolean, default: false },
  error: { type: String, default: '' },
  mode: { type: String, default: 'install' }
})

defineEmits(['close', 'refresh'])
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="extension-gate-backdrop" role="presentation">
      <section class="extension-gate" role="dialog" aria-modal="true" aria-labelledby="extension-gate-title">
        <header class="extension-gate-header">
          <div>
            <span class="gate-kicker"><i aria-hidden="true"></i>{{ mode === 'update' ? '浏览器助手更新' : '首次使用准备' }}</span>
            <h2 id="extension-gate-title">{{ mode === 'update' ? '更新浏览器助手' : '连接你的 BOSS 工作台' }}</h2>
            <p>{{ mode === 'update' ? '旧版助手不具备安全重试能力，更新后才能继续真实发送。' : '真实岗位和打招呼需要浏览器助手读取你当前已登录的 BOSS 页面。' }}</p>
          </div>
          <button type="button" class="gate-close" aria-label="关闭安装说明" @click="$emit('close')">×</button>
        </header>

        <div class="bridge-rail" aria-label="浏览器助手连接路径">
          <div><span>平台</span><strong>{{ appHost }}</strong></div>
          <i aria-hidden="true"><b></b></i>
          <div class="bridge-core"><span>本机</span><strong>浏览器助手</strong></div>
          <i aria-hidden="true"><b></b></i>
          <div><span>登录态</span><strong>BOSS 直聘</strong></div>
        </div>

        <ol class="gate-steps">
          <li><b>1</b><div><strong>下载并解压</strong><p>把 ZIP 解压到一个固定文件夹，安装后不要删除。</p></div></li>
          <li><b>2</b><div><strong>加载浏览器助手</strong><p>在 Chrome 或 Edge 扩展管理页开启开发者模式，加载解压后的文件夹。</p></div></li>
          <li><b>3</b><div><strong>刷新当前页面</strong><p>安装完成后回到这里，刷新页面即可自动连接。</p></div></li>
        </ol>

        <p v-if="error" class="gate-error" role="alert">{{ error }}</p>

        <div class="gate-actions">
          <a class="gate-download" :href="BOSS_EXTENSION_DOWNLOAD_URL" download>{{ mode === 'update' ? '下载最新版助手 ZIP' : '下载浏览器助手 ZIP' }}</a>
          <button type="button" class="gate-refresh" @click="$emit('refresh')">安装后刷新并检测</button>
        </div>

        <footer class="gate-footer">
          <div class="gate-guide-callout">
            <span class="guide-mark" aria-hidden="true">?</span>
            <span><strong>安装遇到问题？</strong><small>查看完整图文教程</small></span>
          </div>
          <div class="guide-links">
            <a class="guide-link chrome-guide" :href="bossExtensionGuideUrl('chrome')" target="_blank" rel="noreferrer">
              <b aria-hidden="true">C</b><span>Google Chrome</span><i aria-hidden="true">↗</i>
            </a>
            <a class="guide-link edge-guide" :href="bossExtensionGuideUrl('edge')" target="_blank" rel="noreferrer">
              <b aria-hidden="true">E</b><span>Microsoft Edge</span><i aria-hidden="true">↗</i>
            </a>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.extension-gate-backdrop{position:fixed;z-index:1900;inset:0;display:grid;place-items:center;background:rgba(8,25,54,.58);padding:28px;backdrop-filter:blur(10px)}
.extension-gate{width:min(760px,calc(100vw - 40px));overflow:hidden;border:1px solid #c8d9ef;border-radius:22px;background:#fff;color:#14213d;box-shadow:0 30px 100px rgba(5,29,66,.3);font-family:var(--ui-font-body,Inter,"Segoe UI Variable Text","PingFang SC",sans-serif)}
.extension-gate-header{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding:25px 28px 19px;background:linear-gradient(135deg,#f8fbff 0%,#eef5ff 100%)}
.gate-kicker{display:inline-flex;align-items:center;gap:8px;color:#0964ca;font:800 11px/1.2 var(--ui-font-utility,Consolas,monospace);letter-spacing:.08em}.gate-kicker i{width:8px;height:8px;border-radius:50%;background:#e3a12e;box-shadow:0 0 0 5px rgba(227,161,46,.13)}
.extension-gate h2{margin:10px 0 5px;font:800 28px/1.15 var(--ui-font-display,"Segoe UI Variable Display","PingFang SC",sans-serif);letter-spacing:-.035em}.extension-gate-header p{margin:0;color:#61728d;font-size:13px;line-height:1.65}
.gate-close{display:grid;width:36px;height:36px;flex:none;place-items:center;border:1px solid #d2deed;border-radius:50%;background:#fff;color:#718198;cursor:pointer;font-size:23px}
.bridge-rail{display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr;align-items:center;border-block:1px solid #e4ecf6;background:#fbfdff;padding:15px 28px}.bridge-rail>div{display:grid;min-height:58px;align-content:center;gap:4px;border:1px solid #d7e4f4;border-radius:12px;background:#fff;padding:10px 13px}.bridge-rail span{color:#8190a6;font-size:9px;font-weight:800;letter-spacing:.08em}.bridge-rail strong{font-size:12px}.bridge-rail>i{position:relative;height:2px;background:#c8d9ef}.bridge-rail>i b{position:absolute;top:-3px;width:8px;height:8px;border-radius:50%;background:#0a6ad8;animation:bridge-travel 1.8s ease-in-out infinite}.bridge-core{border-color:#88b6ef!important;background:#f2f7ff!important}.bridge-core strong{color:#075bb7}
.gate-steps{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0;padding:22px 28px;list-style:none}.gate-steps li{display:grid;grid-template-columns:30px 1fr;align-items:start;gap:10px;border:1px solid #e0e8f2;border-radius:13px;background:#fff;padding:13px}.gate-steps b{display:grid;width:30px;height:30px;place-items:center;border-radius:9px;background:#eaf3ff;color:#0964ca;font:850 12px var(--ui-font-utility,Consolas,monospace)}.gate-steps strong{font-size:12px}.gate-steps p{margin:5px 0 0;color:#73839a;font-size:10px;line-height:1.55}
.gate-error{margin:-7px 28px 17px;border-left:3px solid #d28a22;border-radius:0 8px 8px 0;background:#fff8e9;color:#78551c;padding:8px 11px;font-size:11px}
.gate-actions{display:flex;align-items:center;gap:10px;padding:0 28px 22px}.gate-actions a,.gate-actions button{display:inline-flex;min-height:42px;align-items:center;justify-content:center;border-radius:10px;padding:0 16px;font-size:12px;font-weight:850;text-decoration:none}.gate-download{border:1px solid #0869d8;background:#0869d8;color:#fff;box-shadow:0 8px 20px rgba(8,105,216,.18)}.gate-refresh{border:1px solid #bcd0e8;background:#fff;color:#31587f;cursor:pointer}
.gate-footer{display:flex;align-items:center;justify-content:space-between;gap:18px;border-top:1px solid #cfe0f4;background:linear-gradient(135deg,#eef6ff 0%,#f8fbff 100%);padding:16px 28px}.gate-guide-callout{display:flex;align-items:center;gap:11px;color:#15395f}.guide-mark{display:grid;width:34px;height:34px;flex:none;place-items:center;border-radius:10px;background:#0869d8;color:#fff;font:900 17px/1 var(--ui-font-utility,Consolas,monospace);box-shadow:0 7px 16px rgba(8,105,216,.2)}.gate-guide-callout>span:last-child{display:grid;gap:2px}.gate-guide-callout strong{font-size:14px;line-height:1.2}.gate-guide-callout small{color:#53708f;font-size:12px;font-weight:750}.guide-links{display:flex;align-items:center;gap:8px}.guide-link{display:inline-flex;min-height:38px;align-items:center;gap:7px;border:1px solid #b8d0ed;border-radius:10px;background:#fff;color:#075fc2;padding:0 11px;font-size:11px;font-weight:850;text-decoration:none;box-shadow:0 4px 12px rgba(26,77,130,.07);transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}.guide-link b{display:grid;width:20px;height:20px;place-items:center;border-radius:6px;color:#fff;font:900 10px/1 var(--ui-font-utility,Consolas,monospace)}.chrome-guide b{background:#2879d9}.edge-guide b{background:#0b9f92}.guide-link i{color:#6f8dad;font-style:normal;font-size:12px}.guide-link:hover{border-color:#5d9ee7;box-shadow:0 7px 18px rgba(8,105,216,.13);transform:translateY(-1px)}
.gate-close:focus-visible,.gate-actions a:focus-visible,.gate-actions button:focus-visible,.gate-footer a:focus-visible{outline:3px solid rgba(8,105,216,.2);outline-offset:2px}
@keyframes bridge-travel{0%{left:0;opacity:.35}50%{opacity:1}100%{left:calc(100% - 8px);opacity:.35}}
@media(max-width:720px){.extension-gate-backdrop{align-items:end;padding:0}.extension-gate{width:100%;max-height:94dvh;overflow:auto;border-radius:22px 22px 0 0}.bridge-rail{grid-template-columns:1fr 26px 1fr 26px 1fr;padding-inline:18px}.gate-steps{grid-template-columns:1fr;padding-inline:18px}.gate-actions{align-items:stretch;flex-direction:column;padding-inline:18px}.gate-footer{align-items:stretch;flex-direction:column;padding-inline:18px}.guide-links{display:grid;grid-template-columns:1fr 1fr}.guide-link{justify-content:center}}
@media(prefers-reduced-motion:reduce){.bridge-rail>i b{animation:none;left:calc(50% - 4px)}}
</style>
