use axum::{
    extract::{Request, State},
    http::header::AUTHORIZATION,
    middleware::Next,
    response::Response,
};
use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode};
use serde::Deserialize;

use crate::{AppState, error::AppError};

pub struct JwtVerifier {
    key: DecodingKey,
    validation: Validation,
}

impl JwtVerifier {
    /// The public key is read once at startup; no JWKS fetching (docs/02-test-spec.md, Authentication).
    pub fn from_pem_file(path: &str) -> Self {
        let pem = std::fs::read(path).unwrap_or_else(|e| panic!("cannot read JWT public key {path}: {e}"));
        let key = DecodingKey::from_rsa_pem(&pem).expect("JWT public key is not an RSA PEM");
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_issuer(&["bench-idp"]);
        validation.set_audience(&["bench-api"]);
        validation.set_required_spec_claims(&["exp", "iss", "aud", "sub"]);
        Self { key, validation }
    }
}

#[derive(Deserialize)]
struct Claims {
    sub: String,
}

/// The customer id from the token's `sub` claim.
#[derive(Clone, Copy)]
pub struct AuthUser(pub i32);

pub async fn require_auth(State(state): State<AppState>, mut req: Request, next: Next) -> Result<Response, AppError> {
    let token = req
        .headers()
        .get(AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .ok_or(AppError::Unauthorized)?;
    let claims = decode::<Claims>(token, &state.jwt.key, &state.jwt.validation)
        .map_err(|_| AppError::Unauthorized)?
        .claims;
    let customer_id = claims.sub.parse().map_err(|_| AppError::Unauthorized)?;
    req.extensions_mut().insert(AuthUser(customer_id));
    Ok(next.run(req).await)
}
