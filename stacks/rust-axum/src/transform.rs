use std::collections::HashMap;

use axum::Json;
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
pub struct Cart {
    cart_id: String,
    currency: String,
    items: Vec<LineItem>,
}

#[derive(Deserialize)]
struct LineItem {
    line_id: i64,
    product: CartProduct,
    pricing: Pricing,
    quantity: i64,
}

#[derive(Deserialize)]
#[allow(dead_code)] // parsed as part of the typed model, not used by the transform
struct CartProduct {
    id: i64,
    name: String,
    category: String,
    attributes: HashMap<String, String>,
}

#[derive(Deserialize)]
#[allow(dead_code)]
struct Pricing {
    unit: i64,
    currency: String,
    discounts: Vec<Discount>,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
enum Discount {
    Percent { value: i64 },
    Fixed { value: i64 },
}

#[derive(Serialize)]
pub struct CartSummary {
    cart_id: String,
    currency: String,
    item_count: usize,
    total_cents: i64,
    categories: Vec<CategorySummary>,
}

#[derive(Serialize)]
struct CategorySummary {
    category: String,
    count: u32,
    subtotal_cents: i64,
    top_items: Vec<TopItem>,
}

#[derive(Serialize)]
struct TopItem {
    line_id: i64,
    product_id: i64,
    name: String,
    total_cents: i64,
}

/// Test 7: POST /transform
pub async fn transform(Json(cart): Json<Cart>) -> Json<CartSummary> {
    let item_count = cart.items.len();
    let mut total_cents = 0;
    let mut groups: HashMap<String, CategorySummary> = HashMap::new();

    for item in cart.items {
        let line_total = discounted_unit(&item.pricing) * item.quantity;
        total_cents += line_total;
        let group = groups.entry(item.product.category).or_insert_with_key(|category| CategorySummary {
            category: category.clone(),
            count: 0,
            subtotal_cents: 0,
            top_items: Vec::new(),
        });
        group.count += 1;
        group.subtotal_cents += line_total;
        group.top_items.push(TopItem {
            line_id: item.line_id,
            product_id: item.product.id,
            name: normalize_name(&item.product.name),
            total_cents: line_total,
        });
    }

    let mut categories: Vec<CategorySummary> = groups.into_values().collect();
    for c in &mut categories {
        c.top_items.sort_unstable_by(|a, b| b.total_cents.cmp(&a.total_cents).then(a.line_id.cmp(&b.line_id)));
        c.top_items.truncate(3);
    }
    categories.sort_unstable_by(|a, b| b.subtotal_cents.cmp(&a.subtotal_cents).then_with(|| a.category.cmp(&b.category)));

    Json(CartSummary { cart_id: cart.cart_id, currency: cart.currency, item_count, total_cents, categories })
}

/// Discounts apply to the unit price in order; integer cents, rounding toward zero.
fn discounted_unit(pricing: &Pricing) -> i64 {
    pricing.discounts.iter().fold(pricing.unit, |unit, d| match d {
        Discount::Percent { value } => unit - unit * value / 100,
        Discount::Fixed { value } => (unit - value).max(0),
    })
}

/// Trim, lowercase and collapse whitespace runs to a single space.
fn normalize_name(name: &str) -> String {
    name.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()
}
