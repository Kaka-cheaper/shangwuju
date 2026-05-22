"""auth.providers —— OAuth provider 抽象 + 3 个 stub。

设计原则：
- Demo 阶段所有 provider 抛 NotImplementedError 含「真接入步骤」锚点
- 真上线时只需在对应 stub 内补上 OAuth 2.0 标准 flow（authorize URL → callback exchange → user info）
- 业务代码（main.py 的 endpoint）已经按 OAuthProvider 协议写，不用改

OAuth 2.0 flow（所有 provider 通用）：
    1. 前端发 GET /auth/<provider>/authorize → 后端构造 authorize_url 并 302 跳转
    2. 用户在 provider 端授权 → provider 跳回 /auth/<provider>/callback?code=xxx
    3. 后端用 code 调 provider token endpoint 换 access_token
    4. 用 access_token 调 provider userinfo endpoint 拿 openid / unionid / email
    5. 后端按 provider+openid 在 DB 找/建 user_id
    6. 写 session cookie / 颁发 JWT
    7. 前端跳回原 URL 带 user_id

env 变量（每个 provider 独立）：
    WECHAT_APP_ID / WECHAT_APP_SECRET   — 微信开放平台
    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — Google Cloud Console
    DINGTALK_APP_KEY / DINGTALK_APP_SECRET — 钉钉开放平台
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


# ============================================================
# 数据结构
# ============================================================


@dataclass(frozen=True)
class AuthRequest:
    """构造 authorize_url 所需的最小数据。"""

    redirect_uri: str
    state: str  # CSRF 防护，回调时校验
    scopes: list[str]


@dataclass(frozen=True)
class AuthCallback:
    """callback 端点解析后的 provider 数据。"""

    code: str
    state: str  # 与 AuthRequest.state 校验一致


@dataclass(frozen=True)
class UserInfo:
    """provider 返回的用户基本信息（已按统一 schema 映射）。"""

    provider: str  # "wechat" / "google" / "dingtalk"
    open_id: str  # provider 内唯一标识
    union_id: Optional[str] = None  # 跨应用统一 ID（仅微信开放平台）
    nickname: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None


# ============================================================
# 协议
# ============================================================


@runtime_checkable
class OAuthProvider(Protocol):
    """OAuth 2.0 标准 flow 三步抽象。

    业务代码只面对协议，切 provider 不改 endpoint 实现。
    """

    name: str  # "wechat" / "google" / "dingtalk"

    def build_authorize_url(self, req: AuthRequest) -> str:
        """构造 provider authorize URL（302 跳过去）。"""
        ...

    async def exchange_token(self, callback: AuthCallback) -> str:
        """用 code 换 access_token。"""
        ...

    async def fetch_user_info(self, access_token: str) -> UserInfo:
        """用 token 拉用户信息。"""
        ...


# ============================================================
# 微信扫码登录 stub
# ============================================================


class WechatOAuthProvider:
    """微信开放平台 - 网页扫码登录（OpenID + UnionID）。

    真接入步骤：
    1. 注册 https://open.weixin.qq.com → 创建网站应用 → 拿 AppID / AppSecret
    2. 配 backend/.env：
       WECHAT_APP_ID=wx...
       WECHAT_APP_SECRET=...
       WECHAT_REDIRECT_URI=https://yourdomain.com/auth/wechat/callback
    3. 实现 build_authorize_url：
       https://open.weixin.qq.com/connect/qrconnect?appid={APP_ID}&redirect_uri={URLENC}&response_type=code&scope=snsapi_login&state={STATE}#wechat_redirect
    4. 实现 exchange_token：
       GET https://api.weixin.qq.com/sns/oauth2/access_token?appid=&secret=&code=&grant_type=authorization_code
       → {"access_token","openid","unionid",...}
    5. 实现 fetch_user_info：
       GET https://api.weixin.qq.com/sns/userinfo?access_token=&openid=
       → {"nickname","headimgurl",...}
    6. 单测 + 联调：用 https://open.weixin.qq.com 的「测试号」沙箱

    评分项「商业可行性」直接演示：
        「评委你看这里 → WechatOAuthProvider」
        「实现仅需补完三个方法，OAuth flow 标准；微信开放平台审核 2-3 工作日」
    """

    name = "wechat"

    def build_authorize_url(self, req: AuthRequest) -> str:  # noqa: ARG002
        raise NotImplementedError(
            "WechatOAuthProvider.build_authorize_url 未实现。"
            "Demo 阶段请用 X-User-Id header / persona 切换器；"
            "真接入步骤见 backend/auth/providers.py 文档。"
        )

    async def exchange_token(self, callback: AuthCallback) -> str:  # noqa: ARG002
        raise NotImplementedError(
            "WechatOAuthProvider.exchange_token 未实现；见 providers.py 文档。"
        )

    async def fetch_user_info(self, access_token: str) -> UserInfo:  # noqa: ARG002
        raise NotImplementedError(
            "WechatOAuthProvider.fetch_user_info 未实现；见 providers.py 文档。"
        )


# ============================================================
# Google OAuth stub
# ============================================================


class GoogleOAuthProvider:
    """Google OAuth 2.0 - 海外用户登录。

    真接入步骤：
    1. 注册 https://console.cloud.google.com → 创建 OAuth client → 拿 client_id / client_secret
    2. 配 backend/.env：
       GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
       GOOGLE_CLIENT_SECRET=...
       GOOGLE_REDIRECT_URI=https://yourdomain.com/auth/google/callback
    3. 实现 build_authorize_url：
       https://accounts.google.com/o/oauth2/v2/auth?client_id=&redirect_uri=&response_type=code
       &scope=email%20profile&state={STATE}&access_type=offline
    4. 实现 exchange_token：
       POST https://oauth2.googleapis.com/token
       body: code=&client_id=&client_secret=&redirect_uri=&grant_type=authorization_code
    5. 实现 fetch_user_info：
       GET https://www.googleapis.com/oauth2/v2/userinfo
       Authorization: Bearer {access_token}
    6. ID token 验证（可选）：jose / authlib 校验签名

    建议库：authlib（Python OAuth 2.0 客户端事实标准）+ httpx
    """

    name = "google"

    def build_authorize_url(self, req: AuthRequest) -> str:  # noqa: ARG002
        raise NotImplementedError(
            "GoogleOAuthProvider 未实现；见 backend/auth/providers.py 文档。"
        )

    async def exchange_token(self, callback: AuthCallback) -> str:  # noqa: ARG002
        raise NotImplementedError(
            "GoogleOAuthProvider 未实现；见 backend/auth/providers.py 文档。"
        )

    async def fetch_user_info(self, access_token: str) -> UserInfo:  # noqa: ARG002
        raise NotImplementedError(
            "GoogleOAuthProvider 未实现；见 backend/auth/providers.py 文档。"
        )


# ============================================================
# 钉钉 OAuth stub（企业内部用户场景）
# ============================================================


class DingtalkOAuthProvider:
    """钉钉开放平台 OAuth 2.0 - B 端企业用户场景。

    真接入步骤：
    1. 注册 https://open.dingtalk.com → 创建企业内部应用 → 拿 AppKey / AppSecret
    2. 配 backend/.env：
       DINGTALK_APP_KEY=...
       DINGTALK_APP_SECRET=...
       DINGTALK_REDIRECT_URI=https://yourdomain.com/auth/dingtalk/callback
    3. 实现 build_authorize_url：
       https://login.dingtalk.com/oauth2/auth?client_id={APP_KEY}&redirect_uri={URLENC}
       &response_type=code&scope=openid&state={STATE}
    4. 实现 exchange_token：
       POST https://api.dingtalk.com/v1.0/oauth2/userAccessToken
       JSON: clientId, clientSecret, code, grantType=authorization_code
    5. 实现 fetch_user_info：
       GET https://api.dingtalk.com/v1.0/contact/users/me
       Header: x-acs-dingtalk-access-token: {access_token}

    使用场景：商务局演示给评委时切到「企业版」入口，强调 B 端商业可行性。
    """

    name = "dingtalk"

    def build_authorize_url(self, req: AuthRequest) -> str:  # noqa: ARG002
        raise NotImplementedError(
            "DingtalkOAuthProvider 未实现；见 backend/auth/providers.py 文档。"
        )

    async def exchange_token(self, callback: AuthCallback) -> str:  # noqa: ARG002
        raise NotImplementedError(
            "DingtalkOAuthProvider 未实现；见 backend/auth/providers.py 文档。"
        )

    async def fetch_user_info(self, access_token: str) -> UserInfo:  # noqa: ARG002
        raise NotImplementedError(
            "DingtalkOAuthProvider 未实现；见 backend/auth/providers.py 文档。"
        )


# ============================================================
# 工厂
# ============================================================


_PROVIDERS: dict[str, type] = {
    "wechat": WechatOAuthProvider,
    "google": GoogleOAuthProvider,
    "dingtalk": DingtalkOAuthProvider,
}


def get_oauth_provider(name: Optional[str] = None) -> OAuthProvider:
    """按 name 返回 provider 实例。

    name 缺省时读 OAUTH_PROVIDER env；env 也缺省时返第一个支持的（wechat）。
    Demo 阶段所有 provider 都抛 NotImplementedError，工厂仍工作以便 `/auth/info` 列出可用项。
    """
    name = name or (os.getenv("OAUTH_PROVIDER") or "wechat").strip().lower()
    if name not in _PROVIDERS:
        raise ValueError(
            f"Unknown OAuth provider: {name!r}; valid: {sorted(_PROVIDERS.keys())}"
        )
    return _PROVIDERS[name]()


__all__ = [
    "AuthRequest",
    "AuthCallback",
    "UserInfo",
    "OAuthProvider",
    "WechatOAuthProvider",
    "GoogleOAuthProvider",
    "DingtalkOAuthProvider",
    "get_oauth_provider",
]
