from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from src.web.api import create_app


# 无论从终端、IDE 还是后台进程启动，都以该脚本所在目录作为项目根目录。
# 不能依赖当前工作目录，否则本地私有配置（例如 .env.career-assistant）可能无法被读取。
PROJECT_ROOT = Path(__file__).resolve().parent
# 本地私有配置由求职助手路由在应用启动时读取；开发热重载也会监听该环境文件。
app = create_app(project_root=PROJECT_ROOT)


if __name__ == "__main__":
    # 本地 Web API 的唯一开发入口。Vite 默认代理到此端口。
    port = int(os.environ.get("PREVIEW_SERVER_PORT", "18080"))
    reload_enabled = os.environ.get("PREVIEW_SERVER_RELOAD", "true").lower() in {
        "1",
        "true",
        "yes",
    }

    # Windows、网络盘与桌面应用工作区里，原生文件事件有时不会传给 WatchFiles。
    # 仅开发环境启用轮询，保证保存 Python/YAML 后能够稳定重载。
    if reload_enabled:
        os.environ.setdefault("WATCHFILES_FORCE_POLLING", "true")
        os.environ.setdefault("WATCHFILES_POLL_DELAY_MS", "300")

    reload_dirs = (
        [str(PROJECT_ROOT / "src"), str(PROJECT_ROOT / "config")] if reload_enabled else None
    )

    uvicorn.run(
        "preview_server:app",
        host="127.0.0.1",
        port=port,
        reload=reload_enabled,
        # 监听源码与配置目录，减少权限噪音；保留 .env.career-assistant 让配置热更新更及时。
        reload_dirs=reload_dirs,
        reload_includes=["*.py", "*.yaml", ".env.career-assistant"]
        if reload_enabled
        else None,
    )
