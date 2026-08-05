from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup, Tag

from ..http_client import HttpClient
from ..models import CardPrice, Source

BASE = "https://yuyu-tei.jp"

# 遊々亭のゲームコード（URL の /sell/{code}/ 部分）
GAME_CODES: dict[str, str] = {
    "yugioh": "ygo",
    "rushduel": "yrd",
    "ws": "ws",
    "bs": "bs",
    "zx": "zx",
    "rebirth": "re",
    "onepiece": "opc",
    "pokemon": "poc",
}

PRICE_RE = re.compile(r"([\d,]+)\s*円")
STOCK_NUM_RE = re.compile(r"在庫\s*[:：]\s*(\d+)\s*点")
STOCK_SYM_RE = re.compile(r"在庫\s*[:：]\s*([◯○×✕])")
RARITY_HEADING_RE = re.compile(r"^\s*(.+?)\s*Card\s*List\s*$", re.IGNORECASE)
ALT_RE = re.compile(r"^\s*(?P<id>\S+)\s+(?P<rarity>\S+)\s+(?P<name>.+?)\s*$")


class YuyuteiSource:
    """遊々亭（yuyu-tei.jp）の販売価格スクレイパ。

    2026 年現在の検索 URL:
        https://yuyu-tei.jp/sell/{game}/s/search?search_word=...
    Cloudflare 配下のため、403 時は Playwright にフォールバックする。
    """

    source = Source.YUYUTEI

    def __init__(self, client: HttpClient, *, use_browser_fallback: bool = True) -> None:
        self._client = client
        self._use_browser = use_browser_fallback

    def search_url(self, name: str, game: str = "yugioh") -> str:
        code = GAME_CODES.get(game, "ygo")
        return f"{BASE}/sell/{code}/s/search?search_word={quote(name)}"

    async def search(self, name: str, game: str = "yugioh") -> list[CardPrice]:
        url = self.search_url(name, game)
        try:
            html = await self._client.get_text(url, headers={"Referer": f"{BASE}/"})
        except Exception:
            if not self._use_browser:
                raise
            html = await self._fetch_with_browser(url)
        return self.parse(html, base_url=url)

    # ---------- パース ----------

    @classmethod
    def parse(cls, html: str, *, base_url: str = BASE) -> list[CardPrice]:
        soup = BeautifulSoup(html, "lxml")
        cards: list[CardPrice] = []
        seen: set[str] = set()

        for anchor in soup.select('a[href*="/card/"]'):
            href = anchor.get("href", "")
            if "/card/" not in href:
                continue
            url = urljoin(base_url, href)
            if url in seen:
                continue

            block = cls._container_of(anchor)
            if block is None:
                continue
            text = block.get_text(" ", strip=True)

            price = cls._parse_price(text)
            if price is None:
                continue  # 価格が取れないブロックは商品カードではない

            card_id, rarity, name = cls._from_alt(block)
            if name is None:
                name = cls._parse_name(block, anchor)
            if rarity is None:
                rarity = cls._rarity_from_heading(anchor)

            seen.add(url)
            cards.append(
                CardPrice(
                    source=Source.YUYUTEI,
                    name=name or "",
                    card_id=card_id,
                    rarity=rarity,
                    price=price,
                    condition="プレイ用",  # 遊々亭の通販は基本プレイ用
                    stock=cls._parse_stock(text),
                    url=url,
                    image=cls._parse_image(block),
                )
            )
        return cards

    @staticmethod
    def _container_of(anchor: Tag) -> Tag | None:
        """価格・在庫を含む最小の親要素まで遡る。"""
        node: Tag | None = anchor
        for _ in range(6):
            node = node.parent if node else None
            if node is None or not isinstance(node, Tag):
                return None
            if PRICE_RE.search(node.get_text(" ", strip=True)):
                return node
        return None

    @staticmethod
    def _from_alt(block: Tag) -> tuple[str | None, str | None, str | None]:
        """img[alt] = "QCAC-JP021 QCSE 青眼の白龍" を利用する。"""
        img = block.find("img", alt=True)
        if not img:
            return None, None, None
        m = ALT_RE.match(img["alt"])
        if not m:
            return None, None, None
        card_id = m["id"]
        return (None if card_id == "-" else card_id), m["rarity"], m["name"]

    @staticmethod
    def _parse_name(block: Tag, anchor: Tag) -> str | None:
        for sel in ("h4", "h3", ".card-name"):
            el = block.select_one(sel)
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)
        txt = anchor.get_text(strip=True)
        return txt or None

    @staticmethod
    def _parse_price(text: str) -> int | None:
        # セール表記は "180 円 320 円"(取消線) のように並ぶ→最初＝実売価格
        m = PRICE_RE.search(text)
        return int(m.group(1).replace(",", "")) if m else None

    @staticmethod
    def _parse_stock(text: str) -> int | None:
        m = STOCK_NUM_RE.search(text)
        if m:
            return int(m.group(1))
        m = STOCK_SYM_RE.search(text)
        if m:
            return 0 if m.group(1) in "×✕" else None  # ◯ は「在庫あり(数量非公開)」
        return None

    @staticmethod
    def _parse_image(block: Tag) -> str | None:
        img = block.find("img")
        if not img:
            return None
        src = img.get("src") or img.get("data-src")
        return urljoin(BASE, src) if src else None

    @staticmethod
    def _rarity_from_heading(anchor: Tag) -> str | None:
        """直前の "SE Card List" のような見出しからレアリティを拾う。"""
        for s in anchor.find_all_previous(string=RARITY_HEADING_RE):
            m = RARITY_HEADING_RE.match(str(s))
            if m:
                return m.group(1).strip()
        return None

    # ---------- Playwright フォールバック ----------

    @staticmethod
    async def _fetch_with_browser(url: str) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "遊々亭が Cloudflare で 403 を返しました。"
                "pip install 'solomon2026[browser]' && playwright install chromium "
                "でブラウザフォールバックを有効にしてください。"
            ) from exc

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(locale="ja-JP")
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=45_000)
            html = await page.content()
            await browser.close()
            return html
