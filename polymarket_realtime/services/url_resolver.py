"""URL resolver service for extracting token IDs from Polymarket URLs."""

import re
from typing import Optional
from urllib.parse import urlparse

from polymarket_realtime.api.client import PolymarketClient
from polymarket_realtime.database.repository import Database
from polymarket_realtime.schemas import Market, UrlResolveResult
from polymarket_realtime.utils.logging import get_logger

logger = get_logger(__name__)

# Patterns for extracting slug from various URL formats
SLUG_PATTERNS = [
    re.compile(r"/event/([a-zA-Z0-9_-]+)"),  # /event/slug-name
    re.compile(r"/market/([a-zA-Z0-9_-]+)"),  # /market/slug-name
    re.compile(r"polymarket\.com/([a-zA-Z0-9_-]+)$"),  # polymarket.com/slug-name
]


class UrlResolverError(Exception):
    """Error during URL resolution."""


class UrlResolver:
    """Resolves Polymarket URLs to token IDs.

    Can use cached data from database or fetch fresh data from API.
    """

    def __init__(self, client: PolymarketClient, db: Optional[Database] = None) -> None:
        self._client = client
        self._db = db

    @staticmethod
    def extract_slug(url: str) -> Optional[str]:
        """Extract market slug from a Polymarket URL.

        Supports formats:
        - https://polymarket.com/event/slug-name
        - https://polymarket.com/market/slug-name
        - https://polymarket.com/slug-name

        Args:
            url: Full URL or slug string.

        Returns:
            Extracted slug or None if not found.
        """
        # If it's just a slug (no slashes or http)
        if "/" not in url and "." not in url:
            return url

        # Try to parse as URL
        try:
            parsed = urlparse(url)
            path = parsed.path
        except Exception:
            path = url

        for pattern in SLUG_PATTERNS:
            match = pattern.search(path)
            if match:
                return match.group(1)

        # Fallback: try last path segment
        path_parts = path.strip("/").split("/")
        if path_parts:
            return path_parts[-1]

        return None

    async def resolve(self, url: str, use_cache: bool = True) -> UrlResolveResult:
        """Resolve a URL to token IDs.

        Args:
            url: Polymarket URL or slug.
            use_cache: Whether to check database cache first.

        Returns:
            UrlResolveResult containing token IDs and market info.

        Raises:
            UrlResolverError: If URL is invalid or market not found.
        """
        slug = self.extract_slug(url)
        if not slug:
            raise UrlResolverError(f"Could not extract slug from URL: {url}")

        logger.info("Resolving URL", extra={"ctx_slug": slug, "ctx_use_cache": use_cache})

        # Try cache first
        if use_cache and self._db:
            result = await self._resolve_from_cache(slug)
            if result:
                logger.info("Resolved from cache", extra={"ctx_slug": slug})
                return result

        # Fetch from API
        market = await self._client.get_market_by_slug(slug)
        if not market:
            raise UrlResolverError(f"Market not found for slug: {slug}")

        # Validate tokens
        if len(market.tokens) < 2:
            raise UrlResolverError(
                f"Market has insufficient tokens ({len(market.tokens)}): {slug}"
            )

        # Extract Yes/No token IDs
        yes_token = market.yes_token_id
        no_token = market.no_token_id

        if not yes_token or not no_token:
            # Fallback to first two tokens if outcome matching fails
            yes_token = market.tokens[0].token_id
            no_token = market.tokens[1].token_id

        result = UrlResolveResult(
            slug=slug,
            yes_token=yes_token,
            no_token=no_token,
            market=market,
        )

        # Cache result
        if self._db:
            try:
                await self._db.upsert_market(market)
            except Exception as e:
                logger.warning("Failed to cache market", extra={"ctx_error": str(e)})

        logger.info(
            "Resolved URL",
            extra={
                "ctx_slug": slug,
                "ctx_yes_token": yes_token,
                "ctx_no_token": no_token,
            },
        )

        return result

    async def _resolve_from_cache(self, slug: str) -> Optional[UrlResolveResult]:
        """Try to resolve from database cache."""
        if not self._db:
            return None

        market_data = await self._db.get_market_by_slug(slug)
        if not market_data:
            return None

        token_data = await self._db.get_token_ids_by_market(market_data["id"])
        if len(token_data) < 2:
            return None

        # Build token objects
        tokens = []
        yes_token = None
        no_token = None

        for token_id, outcome in token_data:
            from polymarket_realtime.schemas import Token
            tokens.append(Token(token_id=token_id, outcome=outcome, symbol=None))
            if outcome.lower() == "yes":
                yes_token = token_id
            elif outcome.lower() == "no":
                no_token = token_id

        if not yes_token or not no_token:
            yes_token = token_data[0][0]
            no_token = token_data[1][0]

        market = Market(
            id=market_data["id"],
            slug=market_data["slug"],
            question=market_data["question"],
            category=market_data.get("category"),
            tokens=tokens,
            active=bool(market_data.get("active", 1)),
        )

        return UrlResolveResult(
            slug=slug,
            yes_token=yes_token,
            no_token=no_token,
            market=market,
        )

    async def resolve_multiple(self, urls: list[str]) -> dict[str, UrlResolveResult]:
        """Resolve multiple URLs.

        Args:
            urls: List of URLs to resolve.

        Returns:
            Dict mapping URL to result (excludes failed resolutions).
        """
        results = {}
        for url in urls:
            try:
                result = await self.resolve(url)
                results[url] = result
            except UrlResolverError as e:
                logger.warning("Failed to resolve URL", extra={"ctx_url": url, "ctx_error": str(e)})
        return results
