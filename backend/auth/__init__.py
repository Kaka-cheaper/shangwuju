"""auth —— 第三方 OAuth / 单点登录抽象层（Phase 0.22 接入位）。

Demo 阶段不真做 OAuth：
    评委用 X-User-Id header / persona 切换器即可演示「我是谁」。
    OAuth 是真上线场景必需的（用户注册 / 微信扫码 / Google 登录），
    所以预留 Provider 抽象 + 3 个 stub。

真上线步骤（按 provider 列）：
    见 backend/auth/providers/ 下各文件 docstring。
"""

from .providers import (
    OAuthProvider,
    WechatOAuthProvider,
    GoogleOAuthProvider,
    DingtalkOAuthProvider,
    get_oauth_provider,
)

__all__ = [
    "OAuthProvider",
    "WechatOAuthProvider",
    "GoogleOAuthProvider",
    "DingtalkOAuthProvider",
    "get_oauth_provider",
]
