"""Test 2 (single read) and test 3 (large result set)."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .db import Session
from .models import Product
from .schemas import CategoryPage, IdPath, IntQuery, ProductOut

router = APIRouter()


@router.get("/products/{id}")
async def get_product(id: IdPath, session: Session) -> ProductOut:
    product = await session.get(Product, id)
    if product is None:
        raise HTTPException(404)
    return ProductOut.model_validate(product)


@router.get("/categories/{id}/products")
async def category_products(id: IdPath, session: Session, after: IntQuery = 0) -> CategoryPage:
    # Full entities (description included) are loaded; the summary drops description.
    products = (
        await session.scalars(
            select(Product)
            .where(Product.category_id == id, Product.id > after)
            .order_by(Product.id)
            .limit(50)
        )
    ).all()
    return CategoryPage(
        category_id=id,
        next_after=products[-1].id if products else None,
        items=products,
    )
