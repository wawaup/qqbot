"""
商店爬虫：直接调用链动小铺 shopApi JSON 接口，无需浏览器。

接口：POST {shop_origin}/shopApi/Shop/goodsList
      {"token": "<shop_token>", "keywords": "", "goods_type": "card",
       "current": 1, "pageSize": 999999}

库存判断：extend.stock_count > 0 即有货。
"""
import json
import sys
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from shop.models import Product

OLD_SHOP_HOST = "pay.ldxp.cn"
NEW_SHOP_HOST = "wzyp.cn"


def _rewrite_shop_host(url: str) -> str:
    """官方换域后，把商品链接里的旧前缀换成新域名。"""
    if not url:
        return url
    return url.replace(f"https://{OLD_SHOP_HOST}", f"https://{NEW_SHOP_HOST}").replace(
        f"http://{OLD_SHOP_HOST}", f"https://{NEW_SHOP_HOST}"
    )


def _shop_origin(shop_url: str) -> str:
    parsed = urlparse(shop_url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc or NEW_SHOP_HOST}"


def _extract_token(shop_url: str) -> str:
    """从店铺 URL 提取 token，如 https://wzyp.cn/shop/manboup → manboup"""
    return urlparse(shop_url).path.rstrip("/").split("/")[-1]


def _clean_description(html: str) -> str:
    """把商品详情的 HTML 转成纯文本，供详情指令展示。"""
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


async def scan_all(shop_url: str) -> dict[str, Product]:
    """请求商品列表接口，返回 {goods_key: Product} 字典。"""
    origin = _shop_origin(shop_url)
    token = _extract_token(shop_url)
    payload = {
        "token": token,
        "keywords": "",
        "goods_type": "card",
        "current": 1,
        "pageSize": 999999,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": f"{origin}/",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.post(f"{origin}/shopApi/Shop/goodsList", json=payload)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 1:
        raise RuntimeError(f"接口返回错误: {data.get('msg')}")

    products: dict[str, Product] = {}
    for item in data["data"]["list"]:
        goods_key = item["goods_key"]
        stock_count = item.get("extend", {}).get("stock_count", 0)
        category = item.get("category", {})
        category_name = category.get("name", "其他")

        products[goods_key] = Product(
            id=goods_key,
            title=item["name"],
            url=_rewrite_shop_host(item["link"]),
            category=category_name,
            category_id=category.get("id"),
            in_stock=stock_count > 0,
            price=str(item.get("price", "")),
            description=_clean_description(_rewrite_shop_host(item.get("description", ""))),
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
                print(f"[{p.category}({p.category_id})] {status}  {p.title}")
                print(f"     {p.url}")
        else:
            print(json.dumps(
                {pid: {"title": p.title, "url": p.url,
                       "category": p.category, "category_id": p.category_id,
                       "in_stock": p.in_stock}
                 for pid, p in products.items()},
                ensure_ascii=False, indent=2,
            ))

    asyncio.run(_run())
