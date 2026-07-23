from types import SimpleNamespace

from bot.formatter import _price_label, format_price


def test_format_price_integer_and_one_decimal():
    assert format_price("21.50") == "21.5"
    assert format_price("21.5") == "21.5"
    assert format_price("22.00") == "22"
    assert format_price("22") == "22"
    assert format_price(22) == "22"
    assert format_price("10.05") == "10.1"  # 量化到 1 位
    assert format_price("10.04") == "10"
    assert format_price("") == ""
    assert format_price(None) == ""
    assert format_price("暂无") == "暂无"


def test_price_label_suffix():
    assert _price_label(SimpleNamespace(price="21.50")) == "21.5r · "
    assert _price_label(SimpleNamespace(price="22.00")) == "22r · "
    assert _price_label(SimpleNamespace(price="")) == ""
