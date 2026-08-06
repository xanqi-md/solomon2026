from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from .http_client import HttpClient
from .models import Source
from .service import PriceService, SearchOutcome

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = HttpClient()
    state["client"] = client
    state["service"] = PriceService(client)
    yield
    await client.__aexit__()


app = FastAPI(title="solomon-api 2026", version="2026.1.0", lifespan=lifespan)


@app.get("/api/cards")
async def cards(
    name: str = Query(..., min_length=1, description="カード名（日本語）"),
    source: list[str] | None = Query(None, description="bigweb / yuyutei"),
    game: str = Query("yugioh"),
    in_stock: bool = Query(False),
    exact: bool = Query(True, description="完全一致で検索する"),
    variants: bool = Query(False, description="イラスト違い等も含める"),
):
    try:
        srcs = [Source(s) for s in source] if source else None
    except ValueError:
        raise HTTPException(400, "source は bigweb か yuyutei を指定してください") from None

    service = state["service"]
    if not exact:
        cards, errors = await service.search(
            name, game=game, sources=srcs, in_stock_only=in_stock
        )
        outcome = SearchOutcome(query=name, cards=cards, errors=errors)
    else:
        outcome = await service.search_exact(
            name, game=game, sources=srcs,
            in_stock_only=in_stock, include_variants=variants,
        )

    return {
        "query": {"name": name, "game": game, "exact": exact, "in_stock": in_stock},
        "matched": outcome.matched,
        "suggestions": outcome.suggestions,
        "count": len(outcome.cards),
        "cheapest": {
            k: v.to_dict() for k, v in service.cheapest_by_card(outcome.cards).items()
        },
        "cards": [c.to_dict() for c in outcome.cards],
        "variant_count": len(outcome.variants),
        "errors": outcome.errors,
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
