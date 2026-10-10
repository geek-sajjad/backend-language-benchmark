mod aggregate;
mod auth;
mod catalog;
mod downstream;
mod error;
mod orders;
mod transform;

use std::{env, sync::Arc, time::Duration};

use axum::{
    Json, Router,
    middleware::from_fn_with_state,
    routing::{get, post},
    serve::ListenerExt,
};
use serde::Serialize;
use sqlx::{PgPool, postgres::PgPoolOptions};

#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub http: reqwest::Client,
    pub downstream_url: Arc<str>,
    pub sales: Arc<Vec<aggregate::Sale>>,
    pub jwt: Arc<auth::JwtVerifier>,
}

fn main() {
    let mut runtime = tokio::runtime::Builder::new_multi_thread();
    // Default is one worker thread per core; WORKERS only matters for the 1-core scaling run.
    if let Some(workers) = env::var("WORKERS").ok().filter(|v| !v.is_empty()) {
        runtime.worker_threads(workers.parse().expect("WORKERS must be a number"));
    }
    runtime
        .enable_all()
        .build()
        .expect("failed to build Tokio runtime")
        .block_on(run());
}

async fn run() {
    let jwt = auth::JwtVerifier::from_pem_file(&env_or("JWT_PUBLIC_KEY_FILE", "/run/secrets/jwt_public.pem"));
    let sales = aggregate::build_sales();
    let pool = connect_db().await;
    let state = AppState {
        pool,
        http: reqwest::Client::new(),
        downstream_url: env_or("DOWNSTREAM_URL", "http://127.0.0.1:9000").trim_end_matches('/').into(),
        sales: Arc::new(sales),
        jwt: Arc::new(jwt),
    };

    let secure = api_routes()
        .route("/customers/{id}/orders", get(orders::secure_customer_orders))
        .route("/orders", post(orders::secure_create_order))
        .route_layer(from_fn_with_state(state.clone(), auth::require_auth));

    let app = Router::new()
        .route("/json", get(hello))
        .route("/health", get(|| async { "ok" }))
        .merge(
            api_routes()
                .route("/customers/{id}/orders", get(orders::customer_orders))
                .route("/orders", post(orders::create_order)),
        )
        .nest("/secure", secure)
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080")
        .await
        .expect("failed to bind 0.0.0.0:8080")
        .tap_io(|tcp| {
            let _ = tcp.set_nodelay(true);
        });
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("server error");
}

/// Endpoints served both unprotected (tests 2–8) and under `/secure` (test 9).
fn api_routes() -> Router<AppState> {
    Router::new()
        .route("/products/{id}", get(catalog::get_product))
        .route("/categories/{id}/products", get(catalog::category_products))
        .route("/downstream/{id}", get(downstream::inventory))
        .route("/transform", post(transform::transform))
        .route("/aggregate", get(aggregate::aggregate))
}

#[derive(Serialize)]
struct Message {
    message: &'static str,
}

async fn hello() -> Json<Message> {
    Json(Message { message: "Hello, World!" })
}

async fn connect_db() -> PgPool {
    let url = format!(
        "postgres://{}:{}@{}:{}/{}",
        env_or("DB_USER", "bench"),
        env_or("DB_PASSWORD", "bench"),
        env_or("DB_HOST", "127.0.0.1"),
        env_or("DB_PORT", "5432"),
        env_or("DB_NAME", "bench"),
    );
    let pool_size: u32 = env_or("DB_POOL_SIZE", "64").parse().expect("DB_POOL_SIZE must be a number");
    // The DB may still be starting (compose up, DB reset): retry for up to a minute.
    for attempt in 1..=60 {
        match PgPoolOptions::new().max_connections(pool_size).connect(&url).await {
            Ok(pool) => return pool,
            Err(e) if attempt < 60 => {
                eprintln!("warn: database not reachable yet ({e}), retrying");
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
            Err(e) => panic!("database not reachable: {e}"),
        }
    }
    unreachable!()
}

fn env_or(key: &str, default: &str) -> String {
    env::var(key).ok().filter(|v| !v.is_empty()).unwrap_or_else(|| default.to_owned())
}

async fn shutdown_signal() {
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .expect("failed to install SIGTERM handler");
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {},
        _ = term.recv() => {},
    }
}
