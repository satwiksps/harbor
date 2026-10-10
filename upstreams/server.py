"""Three independent authenticated Streamable HTTP MCP services, one small runtime."""

import hmac
import os
from typing import Annotated, Any, Literal

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from upstreams.commerce import StoreError, WooCommerce
from upstreams.rules import get_policy, quote_shipping, search_policies

Page = Annotated[int, Field(ge=1, le=10000)]
PageSize = Annotated[int, Field(ge=1, le=50)]
OrderId = Annotated[int, Field(gt=0)]
OrderStatus = Literal[
    "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"
]
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


class GatewayOnly:
    """Network policy and a separate bearer secret jointly guard the upstream."""

    def __init__(self, app, token: str):
        if len(token) < 24:
            raise RuntimeError("UPSTREAM_TOKEN must contain at least 24 characters")
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not scope["path"].startswith("/health/"):
            headers = dict(scope.get("headers", []))
            authorization = headers.get(b"authorization", b"")
            if not hmac.compare_digest(authorization, ("Bearer " + self.token).encode()):
                await JSONResponse({"detail": "Gateway credential required"}, 401)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def build_server(kind: str, woo: WooCommerce | None = None) -> MCPServer:
    if kind not in {"woo", "policies", "shipping"}:
        raise ValueError("UPSTREAM_KIND must be woo, policies, or shipping")
    server = MCPServer(f"Commerce {kind}", version="0.1.0")
    if kind == "woo":
        store = woo or WooCommerce(
            os.getenv("WOO_URL", "http://wordpress"),
            os.getenv("WOO_KEY", ""),
            os.getenv("WOO_SECRET", ""),
            os.getenv("WOO_PUBLIC_URL", ""),
            os.getenv("STORE_TIMEZONE", "Asia/Kolkata"),
        )

        async def call(method, **kwargs):
            try:
                return await method(**kwargs)
            except (StoreError, ValueError) as exc:
                raise ToolError(str(exc)) from None

        @server.tool(annotations=READ)
        async def list_products(
            search: Annotated[str, Field(max_length=150)] = "",
            page: Page = 1,
            per_page: PageSize = 20,
        ) -> dict[str, Any]:
            """List store products. Follow total_pages to retrieve additional pages."""
            return await call(store.list_products, search=search, page=page, per_page=per_page)

        @server.tool(annotations=WRITE)
        async def create_product(
            name: Annotated[str, Field(min_length=1, max_length=200)],
            price: Annotated[str, Field(max_length=20)],
            description: Annotated[str, Field(max_length=4000)] = "",
            sku: Annotated[str | None, Field(max_length=100)] = None,
        ) -> dict[str, Any]:
            """Publish a simple product in INR. Price is a decimal string. A unique SKU helps reconcile uncertain results. Never retry an ambiguous timeout automatically."""
            return await call(
                store.create_product, name=name, price=price, description=description, sku=sku
            )

        @server.tool(annotations=READ)
        async def list_orders(
            period: Literal["today", "all"] = "today",
            status: str = "any",
            page: Page = 1,
            per_page: PageSize = 20,
        ) -> dict[str, Any]:
            """List orders, defaulting to today in the store timezone. Follow total_pages. Returns delivery region without customer names, email or street addresses."""
            valid = {
                "any",
                "pending",
                "processing",
                "on-hold",
                "completed",
                "cancelled",
                "refunded",
                "failed",
            }
            if status not in valid:
                raise ToolError("Unsupported order status")
            return await call(
                store.list_orders, period=period, status=status, page=page, per_page=per_page
            )

        @server.tool(annotations=READ)
        async def get_order(order_id: OrderId) -> dict[str, Any]:
            """Get an order by its numeric ID, with line items, totals and delivery region."""
            return await call(store.get_order, order_id=order_id)

        @server.tool(annotations=WRITE)
        async def update_order_status(order_id: OrderId, status: OrderStatus) -> dict[str, Any]:
            """Change an order status. Marking refunded does not issue a payment refund. Requires administrator permission."""
            return await call(store.update_order_status, order_id=order_id, status=status)

    elif kind == "policies":

        @server.tool(name="get_policy", annotations=READ)
        def policy(topic: Literal["shipping", "returns", "fulfillment"]) -> dict[str, Any]:
            """Read the current versioned store policy for shipping, returns or fulfillment."""
            return get_policy(topic)

        @server.tool(name="search", annotations=READ)
        def search(query: Annotated[str, Field(min_length=1, max_length=200)]) -> dict[str, Any]:
            """Search the sample store's policies by words such as shipping or returns."""
            return search_policies(query)
    else:

        @server.tool(name="quote_shipping", annotations=READ)
        def quote(
            subtotal: Annotated[str, Field(max_length=20)],
            country: Annotated[str, Field(min_length=2, max_length=2)] = "IN",
            state: Annotated[str, Field(min_length=2, max_length=2)] = "KA",
            weight_kg: Annotated[str, Field(min_length=1, max_length=20)] = "0.5",
        ) -> dict[str, Any]:
            """Estimate delivery charge in INR using the sample store rate table. Provide order subtotal and delivery region. Not a live carrier booking."""
            try:
                return quote_shipping(subtotal, country, state, weight_kg)
            except ValueError as exc:
                raise ToolError(str(exc)) from None

        @server.tool(name="search", annotations=READ)
        def search_rates(
            query: Annotated[str, Field(min_length=1, max_length=200)],
        ) -> dict[str, Any]:
            """Look up supported delivery coverage and base rate information."""
            return {
                "query": query,
                "country": "IN",
                "standard_base": "59.00",
                "remote_base": "99.00",
                "currency": "INR",
                "sample_rates": True,
            }

    @server.custom_route("/health/live", methods=["GET"])
    async def live(request: Request):
        return JSONResponse({"status": "ok"})

    @server.custom_route("/health/ready", methods=["GET"])
    async def ready(request: Request):
        # This checks the MCP workload, not downstream store health, to avoid restart cascades.
        return JSONResponse({"status": "ok", "service": kind})

    return server


def create_app(kind: str | None = None, token: str | None = None):
    server = build_server(kind or os.getenv("UPSTREAM_KIND", "woo"))
    app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    return GatewayOnly(app, token or os.getenv("UPSTREAM_TOKEN", ""))


if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8001")), access_log=False)
