use std::collections::{HashMap, HashSet};

use axum::{
    Extension, Json,
    extract::{Path, State},
    http::StatusCode,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::json;
use validator::{Validate, ValidationError};

use crate::{
    AppState,
    auth::AuthUser,
    error::{AppError, ValidJson},
};

#[derive(Serialize)]
pub struct OrderItem {
    product_id: i32,
    name: String,
    unit_price_cents: i32,
    quantity: i32,
}

#[derive(Serialize)]
pub struct Order {
    id: i64,
    status: String,
    total_cents: i32,
    created_at: DateTime<Utc>,
    items: Vec<OrderItem>,
}

#[derive(Serialize)]
pub struct CustomerOrders {
    customer_id: i32,
    orders: Vec<Order>,
}

/// Test 4: GET /customers/{id}/orders — 2 queries: the orders, then all their items with product names.
pub async fn customer_orders(
    State(state): State<AppState>,
    Path(customer_id): Path<i32>,
) -> Result<Json<CustomerOrders>, AppError> {
    let rows = sqlx::query!(
        "SELECT id, status, total_cents, created_at FROM orders
         WHERE customer_id = $1 ORDER BY created_at DESC, id DESC LIMIT 10",
        customer_id
    )
    .fetch_all(&state.pool)
    .await?;
    let order_ids: Vec<i64> = rows.iter().map(|o| o.id).collect();

    let mut items_by_order: HashMap<i64, Vec<OrderItem>> = HashMap::with_capacity(order_ids.len());
    if !order_ids.is_empty() {
        let items = sqlx::query!(
            "SELECT oi.order_id, oi.product_id, p.name, oi.unit_price_cents, oi.quantity
             FROM order_items oi JOIN products p ON p.id = oi.product_id
             WHERE oi.order_id = ANY($1) ORDER BY oi.id",
            &order_ids
        )
        .fetch_all(&state.pool)
        .await?;
        for i in items {
            items_by_order.entry(i.order_id).or_default().push(OrderItem {
                product_id: i.product_id,
                name: i.name,
                unit_price_cents: i.unit_price_cents,
                quantity: i.quantity,
            });
        }
    }

    let orders = rows
        .into_iter()
        .map(|o| Order {
            items: items_by_order.remove(&o.id).unwrap_or_default(),
            id: o.id,
            status: o.status,
            total_cents: o.total_cents,
            created_at: o.created_at,
        })
        .collect();
    Ok(Json(CustomerOrders { customer_id, orders }))
}

/// Test 9: only the token's own customer may read their orders.
pub async fn secure_customer_orders(
    state: State<AppState>,
    Extension(AuthUser(sub)): Extension<AuthUser>,
    Path(customer_id): Path<i32>,
) -> Result<Json<CustomerOrders>, AppError> {
    if customer_id != sub {
        return Err(AppError::Forbidden);
    }
    customer_orders(state, Path(customer_id)).await
}

#[derive(Deserialize, Serialize, Validate)]
pub struct OrderLine {
    #[validate(range(min = 1))]
    product_id: i32,
    #[validate(range(min = 1, max = 10))]
    quantity: i32,
}

#[derive(Deserialize, Validate)]
pub struct CreateOrder {
    #[validate(range(min = 1))]
    customer_id: i32,
    #[validate(length(min = 1, max = 10), nested, custom(function = "unique_products"))]
    items: Vec<OrderLine>,
}

/// `/secure/orders`: any `customer_id` in the body is ignored; the token's `sub` is used.
#[derive(Deserialize, Validate)]
pub struct SecureCreateOrder {
    #[validate(length(min = 1, max = 10), nested, custom(function = "unique_products"))]
    items: Vec<OrderLine>,
}

fn unique_products(items: &[OrderLine]) -> Result<(), ValidationError> {
    let mut seen = HashSet::with_capacity(items.len());
    if items.iter().all(|i| seen.insert(i.product_id)) {
        Ok(())
    } else {
        Err(ValidationError::new("duplicate_product_id"))
    }
}

/// Test 5: POST /orders
pub async fn create_order(
    State(state): State<AppState>,
    ValidJson(body): ValidJson<CreateOrder>,
) -> Result<(StatusCode, Json<Order>), AppError> {
    insert_order(&state, body.customer_id, body.items).await
}

pub async fn secure_create_order(
    State(state): State<AppState>,
    Extension(AuthUser(sub)): Extension<AuthUser>,
    ValidJson(body): ValidJson<SecureCreateOrder>,
) -> Result<(StatusCode, Json<Order>), AppError> {
    insert_order(&state, sub, body.items).await
}

/// One transaction: read prices, insert the order and its items, decrement stock atomically.
/// Items are handled in ascending product_id order so concurrent orders lock rows in the same order.
async fn insert_order(
    state: &AppState,
    customer_id: i32,
    mut lines: Vec<OrderLine>,
) -> Result<(StatusCode, Json<Order>), AppError> {
    lines.sort_unstable_by_key(|l| l.product_id);
    let product_ids: Vec<i32> = lines.iter().map(|l| l.product_id).collect();
    let quantities: Vec<i32> = lines.iter().map(|l| l.quantity).collect();

    let mut tx = state.pool.begin().await?;

    let products = sqlx::query!(
        "SELECT id, name, price_cents FROM products WHERE id = ANY($1) ORDER BY id",
        &product_ids
    )
    .fetch_all(&mut *tx)
    .await?;
    if products.len() != lines.len() {
        return Err(AppError::Validation(json!({"items": "unknown product_id"})));
    }
    let prices: Vec<i32> = products.iter().map(|p| p.price_cents).collect();
    let total_cents: i32 = prices.iter().zip(&quantities).map(|(p, q)| p * q).sum();

    let order = sqlx::query!(
        "INSERT INTO orders (customer_id, status, total_cents, created_at)
         VALUES ($1, 'pending', $2, now()) RETURNING id, status, created_at",
        customer_id,
        total_cents
    )
    .fetch_one(&mut *tx)
    .await
    .map_err(|e| match e.as_database_error() {
        Some(db) if db.is_foreign_key_violation() => AppError::Validation(json!({"customer_id": "unknown customer"})),
        _ => e.into(),
    })?;

    sqlx::query!(
        "INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents)
         SELECT $1, * FROM UNNEST($2::int4[], $3::int4[], $4::int4[])",
        order.id,
        &product_ids,
        &quantities,
        &prices
    )
    .execute(&mut *tx)
    .await?;

    for line in &lines {
        sqlx::query!("UPDATE products SET stock = stock - $1 WHERE id = $2", line.quantity, line.product_id)
            .execute(&mut *tx)
            .await?;
    }

    tx.commit().await?;

    let items = products
        .into_iter()
        .zip(&quantities)
        .map(|(p, &quantity)| OrderItem { product_id: p.id, name: p.name, unit_price_cents: p.price_cents, quantity })
        .collect();
    Ok((
        StatusCode::CREATED,
        Json(Order { id: order.id, status: order.status, total_cents, created_at: order.created_at, items }),
    ))
}
