from dataclasses import dataclass, field


@dataclass
class Product:
    id: str
    title: str
    url: str
    category: str
    in_stock: bool
    price: str = ""


@dataclass
class Category:
    name: str
    products: list = field(default_factory=list)
