from shop.models import Product
from storage.state import diff_price_drops
from bot.formatter import format_price_drop_notice


def _product(pid: str, price: str, title: str = "商品") -> Product:
    return Product(
        id=pid,
        title=title,
        url=f"https://wzyp.cn/item/{pid}",
        category="测试",
        in_stock=True,
        price=price,
    )


def test_detects_price_drop():
    old = {"a": {"price": "100"}}
    new = {"a": _product("a", "80", "Plus")}
    drops = diff_price_drops(old, new)
    assert len(drops) == 1
    product, old_price = drops[0]
    assert product.id == "a"
    assert product.price == "80"
    assert old_price == "100"


def test_ignores_same_or_higher_price():
    old = {"a": {"price": "80"}, "b": {"price": "80"}}
    new = {"a": _product("a", "80"), "b": _product("b", "90")}
    assert diff_price_drops(old, new) == []


def test_ignores_drop_below_five_yuan():
    old = {"a": {"price": "100"}, "b": {"price": "100"}}
    new = {"a": _product("a", "96"), "b": _product("b", "95.01")}
    assert diff_price_drops(old, new) == []


def test_notifies_drop_of_exactly_five_yuan():
    old = {"a": {"price": "100"}}
    new = {"a": _product("a", "95")}
    drops = diff_price_drops(old, new)
    assert len(drops) == 1
    assert drops[0][0].price == "95"


def test_ignores_new_product_and_invalid_price():
    old = {"a": {"price": ""}, "b": {"price": "10"}}
    new = {
        "a": _product("a", "8"),
        "b": _product("b", ""),
        "c": _product("c", "1"),
    }
    assert diff_price_drops(old, new) == []


def test_blocks_restock_within_ten_minutes_after_drop():
    from scheduler import tasks

    tasks._price_drop_block.clear()
    dropped = _product("a", "80")
    other = _product("b", "10")
    tasks._mark_price_drop_block([(dropped, "100")])
    kept = tasks._filter_restock_after_price_drop([dropped, other])
    assert [p.id for p in kept] == ["b"]


def test_allows_restock_after_block_window():
    from datetime import timedelta

    from scheduler import tasks

    tasks._price_drop_block.clear()
    dropped = _product("a", "80")
    tasks._mark_price_drop_block([(dropped, "100")])
    tasks._price_drop_block[dropped.id] -= timedelta(seconds=601)
    kept = tasks._filter_restock_after_price_drop([dropped])
    assert [p.id for p in kept] == ["a"]


def test_format_price_drop_notice():
    text = format_price_drop_notice([(_product("a", "80", "Plus 成品"), "100")])
    assert "商品降价" in text
    assert "原价 100r → 现价 80r" in text
    assert "-20r" in text
    assert "https://wzyp.cn/item/a" in text
