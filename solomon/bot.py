from __future__ import annotations

import asyncio
import logging
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from .http_client import HttpClient
from .models import Source
from .service import PriceService
from .sources.bigweb import GAME_IDS

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("solomon.bot")

MAX_FIELDS = 10  # Embed の視認性を考えた表示上限


class SolomonBot(commands.Bot):
    def __init__(self) -> None:
        # メッセージ本文は読まないので intents は既定のままで十分
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.http_client: HttpClient | None = None
        self.service: PriceService | None = None

    async def setup_hook(self) -> None:
        self.http_client = HttpClient()
        self.service = PriceService(self.http_client)

        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            # 開発中は特定ギルドへ同期すると即反映される
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            # グローバル同期は反映まで最大 1 時間ほどかかる
            synced = await self.tree.sync()
        log.info("スラッシュコマンドを %d 件同期しました", len(synced))

    async def close(self) -> None:
        if self.http_client:
            await self.http_client.aclose()
        await super().close()


bot = SolomonBot()


def build_embed(outcome) -> discord.Embed:
    if not outcome.matched:
        embed = discord.Embed(
            title=f"「{outcome.query}」は見つかりませんでした",
            description=(
                "この名前のカードは登録されていません。\n"
                "カタカナや記号の表記をご確認ください。"
            ),
            color=discord.Color.red(),
        )
        if outcome.suggestions:
            embed.add_field(
                name="もしかして",
                value="\n".join(f"・{s}" for s in outcome.suggestions)[:1024],
                inline=False,
            )
        return embed

    cards = outcome.cards
    cheapest = min((c.price for c in cards if c.price), default=None)
    embed = discord.Embed(
        title=f"「{outcome.query}」の販売価格",
        description=f"{len(cards)} 件ヒット（安い順に最大 {MAX_FIELDS} 件を表示）",
        color=discord.Color.blurple(),
    )
    for c in cards[:MAX_FIELDS]:
        price = f"**{c.price:,} 円**" if c.price else "価格非公開 / 売切"
        if c.price and c.price == cheapest:
            price += " 🏆"
        stock = "在庫あり" if c.stock is None else (
            "在庫なし" if c.stock == 0 else f"残り {c.stock} 点"
        )
        shop = "BigWeb" if c.source == Source.BIGWEB else "遊々亭"
        embed.add_field(
            name=f"{c.card_id or '—'} / {c.rarity or '—'}"[:256],
            value=f"{price} ・ {stock}\n[{shop}で見る]({c.url})"[:1024],
            inline=False,
        )

    thumb = next((c.image for c in cards if c.image), None)
    if thumb:
        embed.set_thumbnail(url=thumb)
    if outcome.variants:
        embed.add_field(
            name="派生カード",
            value=f"イラスト違い等が {len(outcome.variants)} 件あります（variants:true で表示）",
            inline=False,
        )
    if outcome.errors:
        embed.add_field(
            name="⚠ 取得できなかったソース",
            value="\n".join(f"`{k}`: {v[:80]}" for k, v in outcome.errors.items())[:1024],
            inline=False,
        )
    embed.set_footer(text="出典: BigWeb / 遊々亭")
    return embed

async def game_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=g, value=g)
        for g in GAME_IDS
        if current.lower() in g.lower()
    ][:25]

SEARCH_TIMEOUT = 45.0

@bot.tree.command(name="price", description="カードの販売価格を BigWeb / 遊々亭 から検索します")
@app_commands.describe(
    name="カード名（日本語）",
    game="ゲームタイトル（既定: yugioh）",
    shop="検索対象の店舗",
    in_stock="在庫がある商品だけ表示する",
    variants="イラスト違い等の派生カードも含める",

    
)
@app_commands.autocomplete(game=game_autocomplete)
@app_commands.choices(
    shop=[
        app_commands.Choice(name="両方", value="both"),
        app_commands.Choice(name="BigWeb のみ", value="bigweb"),
        app_commands.Choice(name="遊々亭 のみ", value="yuyutei"),
    ]
)
async def price(
    interaction: discord.Interaction,
    name: str,
    game: str = "yugioh",
    shop: app_commands.Choice[str] | None = None,
    in_stock: bool = False,
    variants: bool = False,
) -> None:

    # 何よりも先に defer する。ここが 3 秒を超えると即失敗する
    try:
        await interaction.response.defer(thinking=True)
    except discord.HTTPException:
        log.exception("defer に失敗しました（時計のずれ / ネットワークを確認）")
        return

    log.info("検索開始: name=%r game=%s shop=%s", name, game, shop.value if shop else "both")

    if bot.service is None:
        await interaction.followup.send("Bot の初期化が完了していません。", ephemeral=True)
        return

    shop_value = shop.value if shop else "both"
    sources = None if shop_value == "both" else [Source(shop_value)]

    try:
        outcome = await asyncio.wait_for(
            bot.service.search_exact(
                name, game=game, sources=sources,
                in_stock_only=in_stock, include_variants=variants,
            ),
            timeout=SEARCH_TIMEOUT,
        )
    except TimeoutError:
        await interaction.followup.send("検索がタイムアウトしました。もう一度お試しください。")
        return
    except ValueError as exc:
        await interaction.followup.send(f"⚠ {exc}", ephemeral=True)
        return
    except Exception:
        log.exception("検索に失敗しました")
        await interaction.followup.send("検索中にエラーが発生しました。")
        return

    await interaction.followup.send(embed=build_embed(outcome))


@price.error
async def price_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        msg = f"連続実行はできません。{error.retry_after:.0f} 秒後にお試しください。"
    else:
        log.exception("コマンドエラー", exc_info=error)
        msg = "予期しないエラーが発生しました。"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("環境変数 DISCORD_TOKEN が設定されていません（.env を確認）")
    bot.run(token)


if __name__ == "__main__":
    main()
