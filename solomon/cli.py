from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

from .http_client import HttpClient
from .models import CardPrice, Source
from .service import PriceService, SearchOutcome

app = typer.Typer(help="BigWeb / 遊々亭のカード価格を横断表示する (2026 年版)")
console = Console()


@app.command()
def search(
    name: str = typer.Argument(..., help="カード名（日本語）"),
    game: str = typer.Option("yugioh", "--game", "-g", help="yugioh, pokemon, onepiece ..."),
    source: list[str] | None = typer.Option(None, "--source", "-s", help="bigweb / yuyutei"),
    in_stock: bool = typer.Option(False, "--in-stock", help="在庫ありのみ表示"),
    exact: bool = typer.Option(True, "--exact/--partial", help="カード名を完全一致で検索する"),
    variants: bool = typer.Option(False, "--variants", help="イラスト違い等の派生も含める"),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    srcs = [Source(s) for s in source] if source else None
    outcome = asyncio.run(_run(name, game, srcs, in_stock, exact, variants))

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "query": outcome.query,
                    "matched": outcome.matched,
                    "suggestions": outcome.suggestions,
                    "cards": [c.to_dict() for c in outcome.cards[:limit]],
                    "errors": outcome.errors,
                },
                ensure_ascii=False,
            )
        )
        raise typer.Exit()

    if not outcome.matched:
        console.print(
            f"[bold red]「{name}」という名前のカードは見つかりませんでした。[/bold red]"
        )
        if outcome.suggestions:
            console.print("[yellow]もしかして:[/yellow]")
            for s in outcome.suggestions:
                console.print(f"  • {s}")
        else:
            console.print(
                "[dim]カード名の表記（カタカナ・記号）をご確認ください。"
                "部分一致で探すには --partial を付けてください。[/dim]"
            )
    else:
        _render(name, outcome.cards[:limit])
        if outcome.variants:
            console.print(
                f"[dim]ほかに派生カード（イラスト違い等）が {len(outcome.variants)} 件あります。"
                f"--variants で表示できます。[/dim]"
            )

    for src, msg in outcome.errors.items():
        console.print(f"[yellow]⚠ {src} の取得に失敗しました: {msg}[/yellow]")


async def _run(name, game, srcs, in_stock, exact, variants):
    async with HttpClient() as client:
        service = PriceService(client)
        if not exact:
            cards, errors = await service.search(
                name, game=game, sources=srcs, in_stock_only=in_stock
            )
            return SearchOutcome(query=name, cards=cards, errors=errors)
        return await service.search_exact(
            name, game=game, sources=srcs,
            in_stock_only=in_stock, include_variants=variants,
        )


def _render(query: str, cards: list[CardPrice]) -> None:
    table = Table(title=f"「{query}」の販売価格", header_style="bold cyan")
    table.add_column("店舗")
    table.add_column("型番")
    table.add_column("カード名", max_width=32)
    table.add_column("レアリティ", max_width=16)
    table.add_column("価格", justify="right")
    table.add_column("在庫", justify="right")

    cheapest = min((c.price for c in cards if c.price), default=None)
    for c in cards:
        price = f"{c.price:,} 円" if c.price else "—"
        if c.price and c.price == cheapest:
            price = f"[bold green]{price}[/bold green]"
        stock = "○" if c.stock is None else ("×" if c.stock == 0 else str(c.stock))
        table.add_row(
            c.source.value, c.card_id or "—", c.name,
            c.rarity or "—", price, stock,
        )

    console.print(table)
    if not cards:
        console.print("[red]該当するカードが見つかりませんでした。[/red]")


if __name__ == "__main__":
    app()
