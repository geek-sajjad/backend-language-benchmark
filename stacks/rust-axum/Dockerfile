# syntax=docker/dockerfile:1
FROM rust:1.99.0-slim-trixie@sha256:2752b332db73fdbb7dc576f06c82ed1f312005784ef913d7e04a28f5f55dc581 AS build
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY .sqlx ./.sqlx
COPY src ./src
# SQLx checks queries against the prepared metadata in .sqlx/ instead of a live database.
ENV SQLX_OFFLINE=true
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/app/target \
    cargo build --release --locked && cp target/release/bench-rust-axum /bench-rust-axum

FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
COPY --from=build /bench-rust-axum /usr/local/bin/bench-rust-axum
USER 65534
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/bench-rust-axum"]
