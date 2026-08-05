from __future__ import annotations

import asyncio
from urllib.parse import urlencode

from ..http_client import HttpClient
from ..models import CardPrice, Source

API_BASE = "https://api.bigweb.co.jp"
WEB_BASE = "https://www.bigweb.co.jp/ja/products"

# /games から取得できるが、よく使うものは定数化しておく
GAME_IDS: dict[str, int] = {
    "yugioh": 9,
    "mtg": 1,
    "pokemon": 170,
    "duelmasters": 4,
    "onepiece": 177,
    "unionarena": 180,
    "dragonball": 183,
    "digimon": 161,
    "gundam": 186,
    "ws": 126,
    "vanguard": 144,
    "shadowverse": 175,
    "lorcana": 182,
    "hololive": 185,
}


class BigWebSource:
    """BigWeb は 2024 年以降 JSON API (api.bigweb.co.jp) を公開している。

    注意: game_id / name / page 以外の未知パラメータを付けると 500 を返す。
    """

    source = Source.BIGWEB

    def __init__(self, client: HttpClient, *, max_pages: int = 3) -> None:
        self._client = client
        self._max_pages = max_pages

    async def list_games(self) -> dict[str, int]:
        data = await self._client.get_json(f"{API_BASE}/games")
        return {g["title"]: g["id"] for g in data}

    def search_url(self, name: str, game: str = "yugioh") -> str:
        q = urlencode({"game_id": GAME_IDS.get(game, 9), "name": name})
        return f"{API_BASE}/products?{q}"

    async def search(self, name: str, game: str = "yugioh") -> list[CardPrice]:
        game_id = GAME_IDS.get(game)
        if game_id is None:
            raise ValueError(f"未知のゲーム指定: {game}")

        first = await self._fetch_page(game_id, name, 1)
        results = [self._to_card(i) for i in first.get("items", [])]

        page_count = int(first.get("pagenate", {}).get("pageCount", 1) or 1)
        pages = range(2, min(page_count, self._max_pages) + 1)
        if pages:
            rest = await asyncio.gather(
                *(self._fetch_page(game_id, name, p) for p in pages),
                return_exceptions=True,
            )
            for payload in rest:
                if isinstance(payload, Exception):
                    continue
                results.extend(self._to_card(i) for i in payload.get("items", []))

        return self._dedupe(results)

    async def _fetch_page(self, game_id: int, name: str, page: int) -> dict:
        q = urlencode({"game_id": game_id, "name": name, "page": page})
        return await self._client.get_json(f"{API_BASE}/products?{q}")

    @staticmethod
    def _to_card(item: dict) -> CardPrice:
        game_code = (item.get("game") or {}).get("code") or "yugioh"
        price = item.get("price")
        # 売切・価格非公開は price=0, is_hidden_price=true で返る
        if item.get("is_hidden_price") or not price:
            price = None
        return CardPrice(
            source=Source.BIGWEB,
            name=item.get("name", "").strip(),
            card_id=(item.get("fname") or "").strip() or None,
            rarity=(item.get("rarity") or {}).get("web"),
            price=price,
            condition=(item.get("condition") or {}).get("web"),
            stock=int(item.get("stock_count") or 0),
            set_name=(item.get("cardset") or {}).get("web"),
            url=f"{WEB_BASE}/{game_code}/cardViewer/{item['id']}",
            image=item.get("image"),
        )

    @staticmethod
    def _dedupe(cards: list[CardPrice]) -> list[CardPrice]:
        seen: set[tuple] = set()
        out: list[CardPrice] = []
        for c in cards:
            key = (c.url,)
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out
