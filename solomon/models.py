from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum


class Source(str, Enum):
    BIGWEB = "bigweb"
    YUYUTEI = "yuyutei"


@dataclass(slots=True)
class CardPrice:
    """1商品（＝同一カードでもレアリティ・状態違いは別レコード）を表す。"""

    source: Source
    name: str
    card_id: str | None          # 型番 (例: QCAC-JP021)
    rarity: str | None           # 例: UR / SE / プリズマティックシークレットレア
    price: int | None            # 円。None は「価格非公開・売切」
    condition: str | None        # 例: プレイ用 / 特価[傷含む]
    stock: int | None            # 在庫数。None は「在庫あり(数量非公開)」
    set_name: str | None = None
    url: str | None = None
    image: str | None = None
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def in_stock(self) -> bool:
        return self.stock is None or self.stock > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["fetched_at"] = self.fetched_at.isoformat()
        return d
