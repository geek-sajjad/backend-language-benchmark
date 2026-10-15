"""Pydantic models for request validation and responses shared by several endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import Path, Query
from pydantic import BaseModel, ConfigDict

INT32_MAX = 2**31 - 1

# Ids are Postgres integers: anything outside int32 is a malformed parameter (400).
IdPath = Annotated[int, Path(ge=-INT32_MAX - 1, le=INT32_MAX)]
IntQuery = Annotated[int, Query(ge=-INT32_MAX - 1, le=INT32_MAX)]


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    description: str
    price_cents: int
    stock: int
    category_id: int
    created_at: datetime


class ProductSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    price_cents: int
    stock: int
    created_at: datetime


class CategoryPage(BaseModel):
    category_id: int
    next_after: int | None
    items: list[ProductSummary]


class OrderItemOut(BaseModel):
    product_id: int
    name: str
    unit_price_cents: int
    quantity: int


class OrderOut(BaseModel):
    id: int
    status: str
    total_cents: int
    created_at: datetime
    items: list[OrderItemOut]


class CustomerOrders(BaseModel):
    customer_id: int
    orders: list[OrderOut]
