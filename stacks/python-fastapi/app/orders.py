"""Test 4 (list with join) and test 5 (write transaction), public and under /secure."""

from typing import Self

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from .auth import CurrentCustomer, require_auth
from .db import Session
from .errors import validation_failed
from .models import Order, OrderItem, Product
from .schemas import INT32_MAX, CustomerOrders, IdPath, OrderItemOut, OrderOut


def order_out(order: Order, names: dict[int, str] | None = None) -> OrderOut:
    return OrderOut(
        id=order.id,
        status=order.status,
        total_cents=order.total_cents,
        created_at=order.created_at,
        items=[
            OrderItemOut(
                product_id=item.product_id,
                name=names[item.product_id] if names else item.product.name,
                unit_price_cents=item.unit_price_cents,
                quantity=item.quantity,
            )
            for item in order.items
        ],
    )


async def customer_orders(session: AsyncSession, customer_id: int) -> CustomerOrders:
    # 2 queries: the 10 orders, then their items joined to products (id and name only).
    orders = (
        await session.scalars(
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(10)
            .options(
                selectinload(Order.items)
                .joinedload(OrderItem.product)
                .load_only(Product.id, Product.name)
            )
        )
    ).all()
    return CustomerOrders(customer_id=customer_id, orders=[order_out(o) for o in orders])


# Strict mode: "42" or 1.0 is not an integer here (docs/02, test 5 validation).
class OrderLine(BaseModel):
    model_config = ConfigDict(strict=True)

    product_id: int = Field(ge=1, le=INT32_MAX)
    quantity: int = Field(ge=1, le=10)


class SecureCreateOrder(BaseModel):
    """/secure/orders: any customer_id in the body is ignored; the token's sub is used."""

    model_config = ConfigDict(strict=True)

    items: list[OrderLine] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_products(self) -> Self:
        if len({line.product_id for line in self.items}) != len(self.items):
            raise ValueError("duplicate product_id")
        return self


class CreateOrder(SecureCreateOrder):
    customer_id: int = Field(ge=1, le=INT32_MAX)


async def insert_order(session: AsyncSession, customer_id: int, lines: list[OrderLine]) -> OrderOut:
    """One transaction: read prices, insert the order and its items, decrement stock atomically.
    Items are handled in ascending product_id order so concurrent orders lock rows in the same order."""
    lines = sorted(lines, key=lambda line: line.product_id)
    try:
        async with session.begin():
            products = (
                await session.execute(
                    select(Product.id, Product.name, Product.price_cents)
                    .where(Product.id.in_([line.product_id for line in lines]))
                    .order_by(Product.id)
                )
            ).all()
            if len(products) != len(lines):
                raise validation_failed({"items": "unknown product_id"})

            items = [
                OrderItem(product_id=line.product_id, quantity=line.quantity, unit_price_cents=p.price_cents)
                for line, p in zip(lines, products)
            ]
            order = Order(
                customer_id=customer_id,
                status="pending",
                total_cents=sum(i.unit_price_cents * i.quantity for i in items),
                items=items,
            )
            session.add(order)
            await session.flush()

            for line in lines:
                await session.execute(
                    update(Product)
                    .where(Product.id == line.product_id)
                    .values(stock=Product.stock - line.quantity)
                )
    except IntegrityError:
        # Products were checked above, so this is the customer foreign key.
        raise validation_failed({"customer_id": "unknown customer"}) from None
    return order_out(order, {p.id: p.name for p in products})


public = APIRouter()


@public.get("/customers/{id}/orders")
async def get_customer_orders(id: IdPath, session: Session) -> CustomerOrders:
    return await customer_orders(session, id)


@public.post("/orders", status_code=201)
async def create_order(body: CreateOrder, session: Session) -> OrderOut:
    return await insert_order(session, body.customer_id, body.items)


secure = APIRouter(prefix="/secure", dependencies=[require_auth])


@secure.get("/customers/{id}/orders")
async def secure_customer_orders(id: IdPath, customer: CurrentCustomer, session: Session) -> CustomerOrders:
    if id != customer:
        raise HTTPException(403)
    return await customer_orders(session, id)


@secure.post("/orders", status_code=201)
async def secure_create_order(body: SecureCreateOrder, customer: CurrentCustomer, session: Session) -> OrderOut:
    return await insert_order(session, customer, body.items)
