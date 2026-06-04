"""tool_provider —— 9 个工具的数据源抽象层。

商业演进路径（让评委看到「数据源可切换」的扩展性）：

- Demo（当前）：MockToolProvider 走 backend/tools/ + mock_data/
- MVP（1-2 月）：GaodeToolProvider 接高德 Web Service POI 搜索
- 真产品（3-6 月）：DianpingToolProvider 接大众点评 + 商家直签

切换方式：仅改 .env 中的 DATA_PROVIDER=mock|gaode|dianping，业务代码零改动。

设计要点（参考 AGENTS.md §3.4 Tool 设计纪律）：
- ToolProvider Protocol 锁死 9 个方法签名 —— 实现可换、契约不变
- MockToolProvider 用 ``asyncio.to_thread`` 包同步 Tool —— 避免事件循环阻塞
- Stub Provider 不静默失败，抛 NotImplementedError 含「如何接入」指引
- 不在本模块发明新 Pydantic 模型；全部复用 schemas/tools.py（Agent A 的领域）

不负责：
- Tool 实现逻辑（在 backend/tools/，C owner）
- Mock 数据加载（在 mock_data/ + backend/tools/，C owner）
- 注入到 main.py / 现有 v2 模块（那是 G 的活）
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

# 复用 schemas/tools.py 的 9 对 Input/Output 模型（不发明新模型）
from schemas.tools import (
    BuyTicketInput,
    BuyTicketOutput,
    CheckRestaurantAvailabilityInput,
    CheckRestaurantAvailabilityOutput,
    EstimateRouteTimeInput,
    EstimateRouteTimeOutput,
    GenerateShareMessageInput,
    GenerateShareMessageOutput,
    GetUserProfileInput,
    GetUserProfileOutput,
    OrderExtraServiceInput,
    OrderExtraServiceOutput,
    ReserveRestaurantInput,
    ReserveRestaurantOutput,
    SearchPoisInput,
    SearchPoisOutput,
    SearchRestaurantsInput,
    SearchRestaurantsOutput,
)


# ============================================================
# Protocol —— 9 个工具的统一接口
# ============================================================

@runtime_checkable
class ToolProvider(Protocol):
    """9 个工具的统一接口。

    所有方法签名稳定，实现可换。任何符合本协议的对象都可以作为
    数据源喂给 Agent 编排层 —— 这是「商业演进可切换」的契约基础。

    属性：
        name: 数据源标识，如 "mock" / "gaode" / "dianping"。
              主要用于日志、UI 展示、错误提示，不参与业务分发。
    """

    name: str

    async def search_pois(self, inp: SearchPoisInput) -> SearchPoisOutput: ...

    async def search_restaurants(
        self, inp: SearchRestaurantsInput
    ) -> SearchRestaurantsOutput: ...

    async def check_restaurant_availability(
        self, inp: CheckRestaurantAvailabilityInput
    ) -> CheckRestaurantAvailabilityOutput: ...

    async def estimate_route_time(
        self, inp: EstimateRouteTimeInput
    ) -> EstimateRouteTimeOutput: ...

    async def get_user_profile(
        self, inp: GetUserProfileInput
    ) -> GetUserProfileOutput: ...

    async def reserve_restaurant(
        self, inp: ReserveRestaurantInput
    ) -> ReserveRestaurantOutput: ...

    async def buy_ticket(self, inp: BuyTicketInput) -> BuyTicketOutput: ...

    async def order_extra_service(
        self, inp: OrderExtraServiceInput
    ) -> OrderExtraServiceOutput: ...

    async def generate_share_message(
        self, inp: GenerateShareMessageInput
    ) -> GenerateShareMessageOutput: ...


# ============================================================
# MockToolProvider —— 走 backend/tools/ 现有实现
# ============================================================

class MockToolProvider:
    """Demo 默认数据源：复用 backend/tools/ 的同步实现。

    所有方法用 ``asyncio.to_thread`` 把同步 Tool 包成 async ——
    避免在事件循环中长时间阻塞（mock_data 加载是文件 I/O）。
    """

    name = "mock"

    async def search_pois(self, inp: SearchPoisInput) -> SearchPoisOutput:
        from tools.search_pois import search_pois as _impl

        return await asyncio.to_thread(_impl, inp)

    async def search_restaurants(
        self, inp: SearchRestaurantsInput
    ) -> SearchRestaurantsOutput:
        from tools.search_restaurants import search_restaurants as _impl

        return await asyncio.to_thread(_impl, inp)

    async def check_restaurant_availability(
        self, inp: CheckRestaurantAvailabilityInput
    ) -> CheckRestaurantAvailabilityOutput:
        from tools.check_restaurant_availability import (
            check_restaurant_availability as _impl,
        )

        return await asyncio.to_thread(_impl, inp)

    async def estimate_route_time(
        self, inp: EstimateRouteTimeInput
    ) -> EstimateRouteTimeOutput:
        from tools.estimate_route_time import estimate_route_time as _impl

        return await asyncio.to_thread(_impl, inp)

    async def get_user_profile(
        self, inp: GetUserProfileInput
    ) -> GetUserProfileOutput:
        from tools.get_user_profile import get_user_profile as _impl

        return await asyncio.to_thread(_impl, inp)

    async def reserve_restaurant(
        self, inp: ReserveRestaurantInput
    ) -> ReserveRestaurantOutput:
        from tools.reserve_restaurant import reserve_restaurant as _impl

        return await asyncio.to_thread(_impl, inp)

    async def buy_ticket(self, inp: BuyTicketInput) -> BuyTicketOutput:
        from tools.buy_ticket import buy_ticket as _impl

        return await asyncio.to_thread(_impl, inp)

    async def order_extra_service(
        self, inp: OrderExtraServiceInput
    ) -> OrderExtraServiceOutput:
        from tools.order_extra_service import order_extra_service as _impl

        return await asyncio.to_thread(_impl, inp)

    async def generate_share_message(
        self, inp: GenerateShareMessageInput
    ) -> GenerateShareMessageOutput:
        from tools.generate_share_message import generate_share_message as _impl

        return await asyncio.to_thread(_impl, inp)


# ============================================================
# 商业演进 Stub —— 真接入时只换实现
# ============================================================

# 接入指引文档锚点（评委切换时会在错误里看到这条提示）
_GAODE_DOC_HINT = (
    "Gaode integration: 高德 Web Service POI 搜索接入步骤见 "
    "docs/06-business/01-数据源切换路径.md §高德接入"
)
_DIANPING_DOC_HINT = (
    "Dianping integration: 大众点评开放 API 接入步骤见 "
    "docs/06-business/01-数据源切换路径.md §大众点评接入"
)


def _gaode_not_impl(method: str) -> NotImplementedError:
    return NotImplementedError(
        f"[GaodeToolProvider.{method}] 高德数据源尚未接入。{_GAODE_DOC_HINT}"
    )


def _dianping_not_impl(method: str) -> NotImplementedError:
    return NotImplementedError(
        f"[DianpingToolProvider.{method}] 大众点评数据源尚未接入。{_DIANPING_DOC_HINT}"
    )


class GaodeToolProviderStub:
    """高德 Web Service Stub —— 商业演进 Milestone 2 接入点。

    每个方法抛 NotImplementedError 含详细「如何接入」提示。评委切到
    DATA_PROVIDER=gaode 时会在 Tool 调用链路里看到友好错误，证明：

    1. 抽象层确实做出来了（不是 demo 写死）
    2. 真接入有明确文档锚点（不是「以后再说」）

    真接入时 9 个方法逐个替换为 httpx 调高德 OpenAPI 即可，业务代码零改动。
    """

    name = "gaode"

    async def search_pois(self, inp: SearchPoisInput) -> SearchPoisOutput:
        raise _gaode_not_impl("search_pois")

    async def search_restaurants(
        self, inp: SearchRestaurantsInput
    ) -> SearchRestaurantsOutput:
        raise _gaode_not_impl("search_restaurants")

    async def check_restaurant_availability(
        self, inp: CheckRestaurantAvailabilityInput
    ) -> CheckRestaurantAvailabilityOutput:
        raise _gaode_not_impl("check_restaurant_availability")

    async def estimate_route_time(
        self, inp: EstimateRouteTimeInput
    ) -> EstimateRouteTimeOutput:
        raise _gaode_not_impl("estimate_route_time")

    async def get_user_profile(
        self, inp: GetUserProfileInput
    ) -> GetUserProfileOutput:
        raise _gaode_not_impl("get_user_profile")

    async def reserve_restaurant(
        self, inp: ReserveRestaurantInput
    ) -> ReserveRestaurantOutput:
        raise _gaode_not_impl("reserve_restaurant")

    async def buy_ticket(self, inp: BuyTicketInput) -> BuyTicketOutput:
        raise _gaode_not_impl("buy_ticket")

    async def order_extra_service(
        self, inp: OrderExtraServiceInput
    ) -> OrderExtraServiceOutput:
        raise _gaode_not_impl("order_extra_service")

    async def generate_share_message(
        self, inp: GenerateShareMessageInput
    ) -> GenerateShareMessageOutput:
        raise _gaode_not_impl("generate_share_message")


class DianpingToolProviderStub:
    """大众点评 Stub —— 商业演进 Milestone 3 接入点。

    与 GaodeToolProviderStub 同款抛错策略，提示文档锚点指向
    docs/06-business/01-数据源切换路径.md 的大众点评章节。
    """

    name = "dianping"

    async def search_pois(self, inp: SearchPoisInput) -> SearchPoisOutput:
        raise _dianping_not_impl("search_pois")

    async def search_restaurants(
        self, inp: SearchRestaurantsInput
    ) -> SearchRestaurantsOutput:
        raise _dianping_not_impl("search_restaurants")

    async def check_restaurant_availability(
        self, inp: CheckRestaurantAvailabilityInput
    ) -> CheckRestaurantAvailabilityOutput:
        raise _dianping_not_impl("check_restaurant_availability")

    async def estimate_route_time(
        self, inp: EstimateRouteTimeInput
    ) -> EstimateRouteTimeOutput:
        raise _dianping_not_impl("estimate_route_time")

    async def get_user_profile(
        self, inp: GetUserProfileInput
    ) -> GetUserProfileOutput:
        raise _dianping_not_impl("get_user_profile")

    async def reserve_restaurant(
        self, inp: ReserveRestaurantInput
    ) -> ReserveRestaurantOutput:
        raise _dianping_not_impl("reserve_restaurant")

    async def buy_ticket(self, inp: BuyTicketInput) -> BuyTicketOutput:
        raise _dianping_not_impl("buy_ticket")

    async def order_extra_service(
        self, inp: OrderExtraServiceInput
    ) -> OrderExtraServiceOutput:
        raise _dianping_not_impl("order_extra_service")

    async def generate_share_message(
        self, inp: GenerateShareMessageInput
    ) -> GenerateShareMessageOutput:
        raise _dianping_not_impl("generate_share_message")


# ============================================================
# 工厂函数 —— 从 .env 解析 DATA_PROVIDER
# ============================================================

_VALID_PROVIDERS = ("mock", "gaode", "dianping")


def get_tool_provider() -> ToolProvider:
    """根据 .env DATA_PROVIDER 返回对应实现。

    - DATA_PROVIDER=mock     → MockToolProvider（默认）
    - DATA_PROVIDER=gaode    → GaodeToolProviderStub
    - DATA_PROVIDER=dianping → DianpingToolProviderStub

    未设置或空字符串视为 mock。非法值抛 ValueError 带友好提示。
    """
    import os

    raw = os.getenv("DATA_PROVIDER") or "mock"
    name = raw.strip().lower()

    if name == "mock" or name == "":
        return MockToolProvider()
    if name == "gaode":
        return GaodeToolProviderStub()
    if name == "dianping":
        return DianpingToolProviderStub()

    valid = "|".join(_VALID_PROVIDERS)
    raise ValueError(
        f"Unknown DATA_PROVIDER: {raw!r}; valid values: {valid}"
    )


__all__ = [
    "ToolProvider",
    "MockToolProvider",
    "GaodeToolProviderStub",
    "DianpingToolProviderStub",
    "get_tool_provider",
]
