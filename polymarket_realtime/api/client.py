"""Polymarket REST API client."""

from typing import Any, Optional

import httpx

from polymarket_realtime.schemas import Market, Token
from polymarket_realtime.utils.logging import get_logger
from polymarket_realtime.utils.rate_limit import AsyncRateLimiter
from polymarket_realtime.utils.retry import async_retry

logger = get_logger(__name__)


class PolymarketAPIError(Exception):
    """Base exception for Polymarket API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PolymarketClient:
    """Async client for Polymarket REST API.

    Handles rate limiting, retries, and response parsing.
    """

    def __init__(
        self,
        base_url: str = "https://gamma-api.polymarket.com",
        timeout: float = 10.0,
        rate_limit_per_sec: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._rate_limiter = AsyncRateLimiter(rate_limit_per_sec)
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazy initialization of HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("API client closed")

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Make an HTTP request with rate limiting and retry.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: API endpoint path.
            params: Query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            PolymarketAPIError: On API errors.
        """
        client = await self._ensure_client()
        url = f"{self.base_url}{path}"

        async def do_request() -> httpx.Response:
            async with self._rate_limiter:
                response = await client.request(method, url, params=params)
                response.raise_for_status()
                return response

        try:
            response = await async_retry(
                do_request,
                attempts=3,
                base_wait=0.5,
                max_wait=5.0,
                retry_on=(httpx.HTTPStatusError, httpx.TransportError),
            )
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "API request failed",
                extra={
                    "ctx_url": url,
                    "ctx_status": e.response.status_code,
                    "ctx_body": e.response.text[:500],
                },
            )
            raise PolymarketAPIError(
                f"API error: {e.response.status_code}",
                status_code=e.response.status_code,
            ) from e
        except httpx.TransportError as e:
            logger.error("API transport error", extra={"ctx_url": url, "ctx_error": str(e)})
            raise PolymarketAPIError(f"Transport error: {e}") from e

    # ==================== Market Endpoints ====================

    async def list_markets(
        self,
        *,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
        category: Optional[str] = None,
        slug: Optional[str] = None,
    ) -> list[Market]:
        """List markets with optional filters.

        Args:
            active: Filter by active status.
            limit: Maximum results per page.
            offset: Pagination offset.
            category: Filter by category (e.g., "Politics").
            slug: Filter by exact slug match.

        Returns:
            List of Market objects.
        """
        params: dict[str, Any] = {
            "active": str(active).lower(),
            "limit": limit,
            "offset": offset,
        }
        if category:
            params["category"] = category
        if slug:
            params["slug"] = slug

        data = await self._request("GET", "/markets", params=params)

        if not isinstance(data, list):
            logger.warning("Unexpected response format", extra={"ctx_data": str(data)[:200]})
            return []

        markets = []
        for item in data:
            try:
                market = self._parse_market(item)
                markets.append(market)
            except Exception as e:
                logger.warning(
                    "Failed to parse market",
                    extra={"ctx_error": str(e), "ctx_item": str(item)[:200]},
                )
        return markets

    async def get_market_by_slug(self, slug: str) -> Optional[Market]:
        """Get a single market by its slug.

        Args:
            slug: Market slug from URL.

        Returns:
            Market object or None if not found.
        """
        markets = await self.list_markets(slug=slug, limit=1)
        return markets[0] if markets else None

    async def get_market_by_id(self, market_id: str) -> Optional[Market]:
        """Get a single market by its ID.

        Args:
            market_id: Market UUID.

        Returns:
            Market object or None if not found.
        """
        try:
            data = await self._request("GET", f"/markets/{market_id}")
            return self._parse_market(data)
        except PolymarketAPIError as e:
            if e.status_code == 404:
                return None
            raise

    def _parse_market(self, data: dict[str, Any]) -> Market:
        """Parse market data from API response.

        Handles various response formats from Polymarket API.
        """
        tokens = []

        # Handle different token formats
        raw_tokens = data.get("tokens", [])
        if isinstance(raw_tokens, list):
            for t in raw_tokens:
                if isinstance(t, dict):
                    token_id = t.get("token_id") or t.get("tokenId") or ""
                    tokens.append(Token(
                        token_id=str(token_id),
                        outcome=t.get("outcome", ""),
                        symbol=t.get("ticker") or t.get("symbol"),
                    ))

        # Also check clobTokenIds format (may be JSON string or list)
        clob_token_ids = data.get("clobTokenIds", [])
        outcomes = data.get("outcomes", [])

        # Parse JSON strings if needed
        if isinstance(clob_token_ids, str):
            try:
                import json
                clob_token_ids = json.loads(clob_token_ids)
            except json.JSONDecodeError:
                clob_token_ids = []

        if isinstance(outcomes, str):
            try:
                import json
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = []

        if clob_token_ids and not tokens:
            for i, token_id in enumerate(clob_token_ids):
                outcome = outcomes[i] if i < len(outcomes) else f"Outcome{i}"
                tokens.append(Token(
                    token_id=str(token_id),
                    outcome=outcome,
                    symbol=None,
                ))

        return Market(
            id=data.get("id", ""),
            slug=data.get("slug", ""),
            question=data.get("question", data.get("title", "")),
            category=data.get("category"),
            tokens=tokens,
            active=data.get("active", True),
            end_date=data.get("endDate") or data.get("end_date"),
        )

    # ==================== Events Endpoints ====================

    async def list_events(
        self,
        *,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List events (containers for markets).

        Args:
            active: Filter by active status.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of event dictionaries.
        """
        params = {
            "active": str(active).lower(),
            "limit": limit,
            "offset": offset,
        }
        data = await self._request("GET", "/events", params=params)
        return data if isinstance(data, list) else []
