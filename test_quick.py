"""Quick test script for Polymarket realtime."""

import asyncio
from polymarket_realtime.api.client import PolymarketClient
from polymarket_realtime.database.repository import Database
from polymarket_realtime.services.url_resolver import UrlResolver
from polymarket_realtime.services.market_scanner import MarketScanner


async def test_api():
    """Test API client."""
    print("=== Testing API Client ===")
    client = PolymarketClient()

    # List a few markets
    markets = await client.list_markets(active=True, limit=3)
    print(f"Found {len(markets)} markets")

    for m in markets[:2]:
        print(f"  - {m.question[:50]}...")
        print(f"    Tokens: {[t.token_id[:20] + '...' for t in m.tokens]}")

    await client.close()
    return markets


async def test_database():
    """Test database operations."""
    print("\n=== Testing Database ===")
    db = Database("test_polymarket.db")
    await db.initialize()

    # Check if we can list markets
    markets = await db.list_active_markets(limit=5)
    print(f"Markets in DB: {len(markets)}")

    await db.close()
    return db


async def test_url_resolver():
    """Test URL resolver."""
    print("\n=== Testing URL Resolver ===")
    client = PolymarketClient()
    resolver = UrlResolver(client)

    # Test slug extraction
    test_urls = [
        "https://polymarket.com/event/will-joe-biden-get-coronavirus-before-the-election",
        "will-joe-biden-get-coronavirus-before-the-election",
    ]

    for url in test_urls:
        slug = resolver.extract_slug(url)
        print(f"  URL: {url[:50]}... -> slug: {slug}")

    await client.close()


async def test_scanner():
    """Test market scanner."""
    print("\n=== Testing Market Scanner ===")
    client = PolymarketClient()
    db = Database("test_polymarket.db")
    await db.initialize()

    scanner = MarketScanner(client, db, page_size=5)

    # Scan just one page
    result = await scanner.scan_all(persist=True)
    print(f"Scanned {result.total_count} markets")

    # Verify in database
    db_markets = await db.list_active_markets(limit=10)
    print(f"Markets now in DB: {len(db_markets)}")

    await client.close()
    await db.close()


async def main():
    """Run all tests."""
    print("Polymarket Realtime - Quick Test\n")

    try:
        await test_api()
        await test_database()
        await test_url_resolver()
        await test_scanner()
        print("\n✓ All tests passed!")
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
