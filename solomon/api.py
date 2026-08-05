from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

from .http_client import HttpClient
from .models import Source
from .service import PriceService

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
):
    try:
        srcs = [Source(s) for s in source] if source else None
    except ValueError:
        raise HTTPException(400, "source は bigweb か yuyutei を指定してください") from None


    result, errors = await state["service"].search(
        name, game=game, sources=srcs, in_stock_only=in_stock
    )
    cheapest = state["service"].cheapest_by_card(result)
    return {
        "query": {"name": name, "game": game, "in_stock": in_stock},
        "count": len(result),
        "cheapest": {k: v.to_dict() for k, v in cheapest.items()},
        "cards": [c.to_dict() for c in result],
        "errors": errors,
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
