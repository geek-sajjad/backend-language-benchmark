use axum::{
    Json,
    extract::{FromRequest, Request, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use validator::Validate;

pub enum AppError {
    NotFound,
    Unauthorized,
    Forbidden,
    Validation(Value),
    BadGateway(String),
    Internal(String),
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, body) = match self {
            AppError::NotFound => (StatusCode::NOT_FOUND, json!({"error": "not_found"})),
            AppError::Unauthorized => (StatusCode::UNAUTHORIZED, json!({"error": "unauthorized"})),
            AppError::Forbidden => (StatusCode::FORBIDDEN, json!({"error": "forbidden"})),
            AppError::Validation(details) => (
                StatusCode::UNPROCESSABLE_ENTITY,
                json!({"error": "validation_failed", "details": details}),
            ),
            AppError::BadGateway(msg) => {
                eprintln!("error: downstream call failed: {msg}");
                (StatusCode::BAD_GATEWAY, json!({"error": "downstream_failed"}))
            }
            AppError::Internal(msg) => {
                eprintln!("error: {msg}");
                (StatusCode::INTERNAL_SERVER_ERROR, json!({"error": "internal"}))
            }
        };
        (status, Json(body)).into_response()
    }
}

impl From<sqlx::Error> for AppError {
    fn from(e: sqlx::Error) -> Self {
        AppError::Internal(e.to_string())
    }
}

/// JSON body that must deserialize and pass `validator` rules; any failure is a 422.
pub struct ValidJson<T>(pub T);

impl<T, S> FromRequest<S> for ValidJson<T>
where
    T: DeserializeOwned + Validate,
    S: Send + Sync,
{
    type Rejection = AppError;

    async fn from_request(req: Request, state: &S) -> Result<Self, Self::Rejection> {
        let Json(value) = Json::<T>::from_request(req, state)
            .await
            .map_err(|e: JsonRejection| AppError::Validation(json!(e.body_text())))?;
        value
            .validate()
            .map_err(|e| AppError::Validation(serde_json::to_value(e).unwrap_or(Value::Null)))?;
        Ok(ValidJson(value))
    }
}
