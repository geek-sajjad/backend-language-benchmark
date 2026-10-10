use std::collections::HashMap;

use axum::{
    Json,
    extract::{Query, State},
};
use serde::{Deserialize, Serialize};

use crate::AppState;

const SALES_SEED: u64 = 0x5EED_BE7C_0001;
const SALES_COUNT: u32 = 50_000;

pub struct Sale {
    #[allow(dead_code)]
    id: u32,
    region: u8,
    category: u8,
    #[allow(dead_code)]
    customer_tier: u8,
    amount_cents: u32,
    quantity: u32,
    #[allow(dead_code)]
    day: u16,
}

struct SplitMix64(u64);

impl SplitMix64 {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

/// The test 8 dataset; fields are drawn in declaration order (conformance/reference/reference.py).
pub fn build_sales() -> Vec<Sale> {
    let mut rng = SplitMix64(SALES_SEED);
    (1..=SALES_COUNT)
        .map(|id| Sale {
            id,
            region: rng.below(8) as u8,
            category: rng.below(50) as u8,
            customer_tier: rng.below(4) as u8,
            amount_cents: 100 + rng.below(100_000) as u32,
            quantity: 1 + rng.below(10) as u32,
            day: rng.below(365) as u16,
        })
        .collect()
}

#[derive(Deserialize)]
pub struct AggregateParams {
    region: u8,
    min_amount: u32,
}

#[derive(Serialize)]
pub struct AggregateResponse {
    region: u8,
    min_amount: u32,
    groups: Vec<Group>,
}

#[derive(Serialize)]
struct Group {
    category: u8,
    count: u64,
    sum_amount: u64,
    min_amount: u32,
    max_amount: u32,
    avg_amount: u64,
    total_quantity: u64,
}

/// Test 8: GET /aggregate?region={r}&min_amount={m}
pub async fn aggregate(State(state): State<AppState>, Query(params): Query<AggregateParams>) -> Json<AggregateResponse> {
    let mut groups: HashMap<u8, Group> = HashMap::new();
    for sale in state.sales.iter() {
        if sale.region == params.region && sale.amount_cents >= params.min_amount {
            let g = groups.entry(sale.category).or_insert(Group {
                category: sale.category,
                count: 0,
                sum_amount: 0,
                min_amount: u32::MAX,
                max_amount: 0,
                avg_amount: 0,
                total_quantity: 0,
            });
            g.count += 1;
            g.sum_amount += u64::from(sale.amount_cents);
            g.min_amount = g.min_amount.min(sale.amount_cents);
            g.max_amount = g.max_amount.max(sale.amount_cents);
            g.total_quantity += u64::from(sale.quantity);
        }
    }

    let mut groups: Vec<Group> = groups.into_values().collect();
    for g in &mut groups {
        g.avg_amount = g.sum_amount / g.count;
    }
    groups.sort_unstable_by(|a, b| b.sum_amount.cmp(&a.sum_amount).then(a.category.cmp(&b.category)));
    groups.truncate(10);

    Json(AggregateResponse { region: params.region, min_amount: params.min_amount, groups })
}
