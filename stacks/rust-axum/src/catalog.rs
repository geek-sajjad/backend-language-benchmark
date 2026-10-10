use axum::{
    Json,
    extract::{Path, Query, State},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::{AppState, error::AppError};

/// Full `products` row, loaded with every column (test 3 requires full entities).
#[derive(Serialize)]
pub struct Product {
    id: i32,
    sku: String,
    name: String,
    description: String,
    price_cents: i32,
    stock: i32,
    category_id: i32,
    created_at: DateTime<Utc>,
}

/// Test 2: GET /products/{id}
pub async fn get_product(State(state): State<AppState>, Path(id): Path<i32>) -> Result<Json<Product>, AppError> {
    sqlx::query_as!(
        Product,
        "SELECT id, sku, name, description, price_cents, stock, category_id, created_at
         FROM products WHERE id = $1",
        id
    )
    .fetch_optional(&state.pool)
    .await?
    .map(Json)
    .ok_or(AppError::NotFound)
}

#[derive(Deserialize)]
pub struct PageParams {
    #[serde(default)]
    after: i32,
}

#[derive(Serialize)]
pub struct ProductSummary {
    id: i32,
    sku: String,
    name: String,
    price_cents: i32,
    stock: i32,
    created_at: DateTime<Utc>,
}

impl From<Product> for ProductSummary {
    fn from(p: Product) -> Self {
        Self { id: p.id, sku: p.sku, name: p.name, price_cents: p.price_cents, stock: p.stock, created_at: p.created_at }
    }
}

#[derive(Serialize)]
pub struct CategoryPage {
    category_id: i32,
    next_after: Option<i32>,
    items: Vec<ProductSummary>,
}

/// Test 3: GET /categories/{id}/products?after={after_id}
pub async fn category_products(
    State(state): State<AppState>,
    Path(category_id): Path<i32>,
    Query(page): Query<PageParams>,
) -> Result<Json<CategoryPage>, AppError> {
    let products = sqlx::query_as!(
        Product,
        "SELECT id, sku, name, description, price_cents, stock, category_id, created_at
         FROM products WHERE category_id = $1 AND id > $2 ORDER BY id LIMIT 100",
        category_id,
        page.after
    )
    .fetch_all(&state.pool)
    .await?;
    let items: Vec<ProductSummary> = products.into_iter().map(ProductSummary::from).collect();
    Ok(Json(CategoryPage { category_id, next_after: items.last().map(|p| p.id), items }))
}
