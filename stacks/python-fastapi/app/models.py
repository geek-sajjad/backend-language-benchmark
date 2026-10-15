"""SQLAlchemy ORM models for the shared schema (db/init/01-schema.sql)."""

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str]
    name: Mapped[str]
    description: Mapped[str]
    price_cents: Mapped[int]
    stock: Mapped[int]
    category_id: Mapped[int]
    created_at: Mapped[datetime]


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int]
    status: Mapped[str]
    total_cents: Mapped[int]
    # The schema has no DEFAULT: now() is rendered into the INSERT and read back through RETURNING.
    created_at: Mapped[datetime] = mapped_column(insert_default=func.now())

    items: Mapped[list["OrderItem"]] = relationship(order_by="OrderItem.id")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    unit_price_cents: Mapped[int]

    product: Mapped[Product] = relationship()
