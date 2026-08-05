from __future__ import annotations

import asyncio
from collections import defaultdict

from .http_client import HttpClient
from .models import CardPrice, Source
from .sources.bigweb import BigWebSource
from .sources.yuyutei import YuyuteiSource


class PriceService:
    def __init__(self, client: HttpClient) -> None:
        self._sources = {
            Source.BIGWEB: BigWebSource(client),
            Source.YUYUTEI: YuyuteiSource(client),
        }

    async def search(
        self,
        name: str,
        *,
        game: str = "yugioh",
        sources: list[Source] | None = None,
        in_stock_only: bool = False,
    ) -> tuple[list[CardPrice], dict[str, str]]:
        """返り値: (カード一覧, ソースごとのエラーメッセージ)"""
        targets = sources or list(self._sources)
        results = await asyncio.gather(
            *(self._sources[s].search(name, game) for s in targets),
            return_exceptions=True,
        )

        cards: list[CardPrice] = []
        errors: dict[str, str] = {}
        for src, res in zip(targets, results):
            if isinstance(res, Exception):
                errors[src.value] = f"{type(res).__name__}: {res}"
            else:
                cards.extend(res)

        if in_stock_only:
            cards = [c for c in cards if c.in_stock and c.price]

        cards.sort(key=lambda c: (c.price is None, c.price or 0))
        return cards, errors

    @staticmethod
    def cheapest_by_card(cards: list[CardPrice]) -> dict[str, CardPrice]:
        """型番ごとの最安値（在庫ありのみ）を返す。"""
        best: dict[str, CardPrice] = {}
        for c in cards:
            if c.price is None or not c.in_stock:
                continue
            key = c.card_id or c.name
            if key not in best or c.price < best[key].price:
                best[key] = c
        return best

    @staticmethod
    def group_by_source(cards: list[CardPrice]) -> dict[Source, list[CardPrice]]:
        grouped: dict[Source, list[CardPrice]] = defaultdict(list)
        for c in cards:
            grouped[c.source].append(c)
        return dict(grouped)
