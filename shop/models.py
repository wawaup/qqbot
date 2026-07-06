from dataclasses import dataclass, field


@dataclass
class Product:
    id: str
    title: str
    url: str
    category: str
    in_stock: bool
    price: str = ""
    category_id: int | None = None
    market_price: str = ""
    stock_count: int = 0
    description: str = ""
    image: str = ""


@dataclass
class Category:
    name: str
    products: list = field(default_factory=list)
