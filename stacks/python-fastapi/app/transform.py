"""Test 7: heavy JSON transform (POST /transform)."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Discount(BaseModel):
    kind: Literal["percent", "fixed"]
    value: int


class Pricing(BaseModel):
    unit: int
    currency: str
    discounts: list[Discount]


class CartProduct(BaseModel):
    id: int
    name: str
    category: str
    attributes: dict[str, str]


class CartItem(BaseModel):
    line_id: int
    quantity: int
    product: CartProduct
    pricing: Pricing


class Cart(BaseModel):
    cart_id: str
    currency: str
    items: list[CartItem]


class TopItem(BaseModel):
    line_id: int
    product_id: int
    name: str
    total_cents: int


class CategorySummary(BaseModel):
    category: str
    count: int
    subtotal_cents: int
    top_items: list[TopItem]


class TransformResult(BaseModel):
    cart_id: str
    currency: str
    item_count: int
    total_cents: int
    categories: list[CategorySummary]


def discounted_unit(pricing: Pricing) -> int:
    """Discounts apply in list order, in integer cents."""
    unit = pricing.unit
    for d in pricing.discounts:
        if d.kind == "percent":
            unit -= unit * d.value // 100
        else:
            unit = max(0, unit - d.value)
    return unit


def normalize_name(name: str) -> str:
    """Trim, lowercase and collapse whitespace runs to one space."""
    return " ".join(name.split()).lower()


@router.post("/transform")
async def transform(cart: Cart) -> TransformResult:
    groups: dict[str, list[TopItem]] = {}
    total = 0
    for item in cart.items:
        line_total = discounted_unit(item.pricing) * item.quantity
        total += line_total
        groups.setdefault(item.product.category, []).append(
            TopItem(
                line_id=item.line_id,
                product_id=item.product.id,
                name=normalize_name(item.product.name),
                total_cents=line_total,
            )
        )

    categories = [
        CategorySummary(
            category=category,
            count=len(items),
            subtotal_cents=sum(i.total_cents for i in items),
            top_items=sorted(items, key=lambda i: (-i.total_cents, i.line_id))[:3],
        )
        for category, items in groups.items()
    ]
    categories.sort(key=lambda c: (-c.subtotal_cents, c.category))
    return TransformResult(
        cart_id=cart.cart_id,
        currency=cart.currency,
        item_count=len(cart.items),
        total_cents=total,
        categories=categories,
    )
