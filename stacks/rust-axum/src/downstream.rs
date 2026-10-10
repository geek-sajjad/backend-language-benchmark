use axum::{
    Json,
    extract::{Path, State},
};
use serde::{Deserialize, Serialize};

use crate::{AppState, error::AppError};

#[derive(Deserialize)]
struct Inventory {
    available: i64,
}

#[derive(Serialize)]
pub struct Stock {
    product_id: i64,
    available: i64,
    source: &'static str,
}

/// Test 6: GET /downstream/{id} — proxies the mock through the shared keep-alive client.
pub async fn inventory(State(state): State<AppState>, Path(id): Path<i64>) -> Result<Json<Stock>, AppError> {
    let inventory: Inventory = state
        .http
        .get(format!("{}/inventory/{id}", state.downstream_url))
        .send()
        .await
        .and_then(|r| r.error_for_status())
        .map_err(|e| AppError::BadGateway(e.to_string()))?
        .json()
        .await
        .map_err(|e| AppError::BadGateway(e.to_string()))?;
    Ok(Json(Stock { product_id: id, available: inventory.available, source: "downstream" }))
}
