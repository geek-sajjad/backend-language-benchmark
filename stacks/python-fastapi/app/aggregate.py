"""Test 8: in-memory aggregation over the splitmix64 sales dataset, in plain Python."""

from dataclasses import dataclass

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .schemas import IntQuery

MASK64 = 0xFFFF_FFFF_FFFF_FFFF
SALES_SEED = 0x5EED_BE7C_0001
SALES_COUNT = 50_000

router = APIRouter()


class SplitMix64:
    def __init__(self, seed: int):
        self.state = seed & MASK64

    def next(self) -> int:
        self.state = (self.state + 0x9E37_79B9_7F4A_7C15) & MASK64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB) & MASK64
        return z ^ (z >> 31)


@dataclass(slots=True, frozen=True)
class Sale:
    id: int
    region: int
    category: int
    customer_tier: int
    amount_cents: int
    quantity: int
    day: int


def build_sales() -> list[Sale]:
    """50,000 records; the six fields are drawn in this exact order per record (docs/02)."""
    rng = SplitMix64(SALES_SEED)
    return [
        Sale(
            id=i,
            region=rng.next() % 8,
            category=rng.next() % 50,
            customer_tier=rng.next() % 4,
            amount_cents=100 + rng.next() % 100_000,
            quantity=1 + rng.next() % 10,
            day=rng.next() % 365,
        )
        for i in range(1, SALES_COUNT + 1)
    ]


class Group:
    __slots__ = ("count", "sum_amount", "min_amount", "max_amount", "total_quantity")

    def __init__(self, sale: Sale):
        self.count = 1
        self.sum_amount = self.min_amount = self.max_amount = sale.amount_cents
        self.total_quantity = sale.quantity

    def add(self, sale: Sale) -> None:
        self.count += 1
        self.sum_amount += sale.amount_cents
        self.min_amount = min(self.min_amount, sale.amount_cents)
        self.max_amount = max(self.max_amount, sale.amount_cents)
        self.total_quantity += sale.quantity


class GroupOut(BaseModel):
    category: int
    count: int
    sum_amount: int
    min_amount: int
    max_amount: int
    avg_amount: int
    total_quantity: int


class AggregateResult(BaseModel):
    region: int
    min_amount: int
    groups: list[GroupOut]


@router.get("/aggregate")
async def aggregate(region: IntQuery, min_amount: IntQuery, request: Request) -> AggregateResult:
    groups: dict[int, Group] = {}
    for sale in request.state.sales:
        if sale.region == region and sale.amount_cents >= min_amount:
            group = groups.get(sale.category)
            if group is None:
                groups[sale.category] = Group(sale)
            else:
                group.add(sale)

    top = sorted(groups.items(), key=lambda kv: (-kv[1].sum_amount, kv[0]))[:10]
    return AggregateResult(
        region=region,
        min_amount=min_amount,
        groups=[
            GroupOut(
                category=category,
                count=g.count,
                sum_amount=g.sum_amount,
                min_amount=g.min_amount,
                max_amount=g.max_amount,
                avg_amount=g.sum_amount // g.count,
                total_quantity=g.total_quantity,
            )
            for category, g in top
        ],
    )
