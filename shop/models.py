from dataclasses import dataclass, field


@dataclass
class Product:
    id: str
    title: str
    url: str
    category: str
    in_stock: bool
    price: str = ""
    stock_count: int = 0
    category_id: int | None = None
    description: str = ""
    description_html: str = ""
    cover_url: str = ""
    detail_image_urls: tuple[str, ...] = ()


@dataclass
class Category:
    name: str
    products: list = field(default_factory=list)
