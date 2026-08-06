from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .http_client import HttpClient
from .models import CardPrice, Source
from .sources.bigweb import BigWebSource
from .sources.yuyutei import YuyuteiSource

# 全角ダッシュ類はすべて長音記号に寄せる
_DASH_MAP = dict.fromkeys(map(ord, "－―—–‐‒-ｰ"), "ー")
# 中黒・空白・句読点は照合時に無視する
_SEPARATOR_RE = re.compile(r"[\s\u3000・･、,．.]+")
# 末尾の括弧書き（イラスト違い版 など）
_PAREN_RE = re.compile(r"[(（\[【〔].*?[)）\]】〕]")


def normalize_name(name: str) -> str:
    """照合用にカード名を正規化する。

    半角カナ・全角英数・ダッシュ・中黒の揺れは吸収するが、
    ひらがな/カタカナの違いは意図的に区別する（誤記を検出するため）。
    """
    s = unicodedata.normalize("NFKC", name)
    s = s.translate(_DASH_MAP)
    s = _SEPARATOR_RE.sub("", s)
    return s.casefold()


def base_name(name: str) -> str:
    """括弧書きを除いた名前。「青眼の白龍(イラスト違い版)」→「青眼の白龍」"""
    return normalize_name(_PAREN_RE.sub("", name))


@dataclass(slots=True)
class SearchOutcome:
    query: str
    cards: list[CardPrice] = field(default_factory=list)
    variants: list[CardPrice] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return bool(self.cards)


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
        """部分一致検索。返り値: (カード一覧, ソースごとのエラー)"""
        targets = sources or list(self._sources)
        results = await asyncio.gather(
            *(self._sources[s].search(name, game) for s in targets),
            return_exceptions=True,
        )

        cards: list[CardPrice] = []
        errors: dict[str, str] = {}
        for src, res in zip(targets, results, strict=True):
            if isinstance(res, Exception):
                errors[src.value] = f"{type(res).__name__}: {res}"
            else:
                cards.extend(res)

        if in_stock_only:
            cards = [c for c in cards if c.in_stock and c.price]

        cards.sort(key=lambda c: (c.price is None, c.price or 0))
        return cards, errors

    async def search_exact(
        self,
        name: str,
        *,
        game: str = "yugioh",
        sources: list[Source] | None = None,
        in_stock_only: bool = False,
        include_variants: bool = False,
    ) -> SearchOutcome:
        """カード名の完全一致で検索する。

        両サイトの検索は部分一致なので、取得結果をこちら側で絞り込む。
        一致するものが無い場合は近い名前を suggestions に入れて返す。
        """
        cards, errors = await self.search(
            name, game=game, sources=sources, in_stock_only=in_stock_only
        )

        target = normalize_name(name)
        exact: list[CardPrice] = []
        variants: list[CardPrice] = []
        for c in cards:
            if normalize_name(c.name) == target:
                exact.append(c)
            elif base_name(c.name) == target:
                variants.append(c)

        if include_variants:
            exact.extend(variants)
            variants = []
        exact.sort(key=lambda c: (c.price is None, c.price or 0))
        variants.sort(key=lambda c: (c.price is None, c.price or 0))

        suggestions: list[str] = []
        if not exact:
            pool = {c.name for c in cards}
            if len(pool) < 5:
                pool |= await self._candidate_names(name, game)
            suggestions = self._rank_suggestions(name, pool)

        return SearchOutcome(
            query=name,
            cards=exact,
            variants=variants,
            suggestions=suggestions,
            errors=errors,
        )

    async def _candidate_names(self, name: str, game: str) -> set[str]:
        """誤記で 0 件だったとき、部分語で再検索して候補名を集める。"""
        queries = self._sub_queries(name)
        if not queries:
            return set()
        bigweb = self._sources[Source.BIGWEB]  # 速い方だけ使う
        results = await asyncio.gather(
            *(bigweb.search(q, game) for q in queries), return_exceptions=True
        )
        names: set[str] = set()
        for r in results:
            if isinstance(r, Exception):
                continue
            names.update(c.name for c in r)
        return names

    @staticmethod
    def _sub_queries(name: str) -> list[str]:
        """「エヴォルだー・テリアス」→「エヴォルだー」「テリアス」「エヴォ」"""
        tokens = [t for t in re.split(r"[\s\u3000・･]+", name) if len(t) >= 2]
        queries = sorted(tokens, key=len, reverse=True)[:2]
        if len(name) >= 4:
            queries.append(name[:3])
        return list(dict.fromkeys(queries))[:3]

    @staticmethod
    def _rank_suggestions(query: str, pool: set[str], limit: int = 5) -> list[str]:
        target = normalize_name(query)
        scored = [
            (SequenceMatcher(None, target, normalize_name(n)).ratio(), n) for n in pool
        ]
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [n for score, n in scored if score >= 0.6][:limit]

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
