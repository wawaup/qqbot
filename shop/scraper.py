"""
商店爬虫：直接调用链动小铺 shopApi JSON 接口，无需浏览器。

接口：POST https://pay.ldxp.cn/shopApi/Shop/goodsList
      {"token": "<shop_token>", "keywords": "", "goods_type": "card",
       "current": 1, "pageSize": 999999}

库存判断：extend.stock_count > 0 即有货。
"""
import json
import sys
from urllib.parse import urlparse

import httpx

from shop.models import Product

API_BASE = "https://pay.ldxp.cn/shopApi/Shop"

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://pay.ldxp.cn/",
}


def _extract_token(shop_url: str) -> str:
    """从店铺 URL 提取 token，如 https://pay.ldxp.cn/shop/manboup → manboup"""
    return urlparse(shop_url).path.rstrip("/").split("/")[-1]


async def scan_all(shop_url: str) -> dict[str, Product]:
    """请求商品列表接口，返回 {goods_key: Product} 字典。"""
    token = _extract_token(shop_url)
    payload = {
        "token": token,
        "keywords": "",
        "goods_type": "card",
        "current": 1,
        "pageSize": 999999,
    }

    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        resp = await client.post(f"{API_BASE}/goodsList", json=payload)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 1:
        raise RuntimeError(f"接口返回错误: {data.get('msg')}")

    products: dict[str, Product] = {}
    for item in data["data"]["list"]:
        goods_key = item["goods_key"]
        stock_count = item.get("extend", {}).get("stock_count", 0)
        category_name = item.get("category", {}).get("name", "其他")

        products[goods_key] = Product(
            id=goods_key,
            title=item["name"],
            url=item["link"],
            category=category_name,
            in_stock=stock_count > 0,
            price=str(item.get("price", "")),
        )

    return products


# ── 调试入口 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from config import SHOP_URL

    async def _run():
        products = await scan_all(SHOP_URL)
        if "--debug" in sys.argv:
            for p in products.values():
                status = "✅有货" if p.in_stock else "❌缺货"
                print(f"[{p.category}] {status}  {p.title}")
                print(f"     {p.url}")
        else:
            print(json.dumps(
                {pid: {"title": p.title, "url": p.url,
                       "category": p.category, "in_stock": p.in_stock}
                 for pid, p in products.items()},
                ensure_ascii=False, indent=2,
            ))

    asyncio.run(_run())
