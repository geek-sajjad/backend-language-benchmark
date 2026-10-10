"""Reference implementation of the PRNG-derived data and the pure-CPU endpoints.

Every stack must produce exactly what this module produces:
  - splitmix64 and the test 8 sales dataset (docs/02-test-spec.md "Shared in-memory dataset")
  - the test 7 transform (POST /transform) and its payload generator
  - the test 8 aggregation (GET /aggregate)
"""

MASK = 0xFFFF_FFFF_FFFF_FFFF

SALES_SEED = 0x5EED_BE7C_0001
SALES_COUNT = 50_000
TRANSFORM_SEED = 0x5EED_BE7C_0007
AGGREGATE_MIN_AMOUNTS = [0, 1_000, 10_000, 50_000, 90_000]


class SplitMix64:
    def __init__(self, seed: int):
        self.state = seed & MASK

    def next(self) -> int:
        self.state = (self.state + 0x9E37_79B9_7F4A_7C15) & MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9) & MASK
        z = ((z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB) & MASK
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        """Uniform-ish value in 0..n-1 (plain modulo, identical in every language)."""
        return self.next() % n


# ---------------------------------------------------------------- test 8: sales dataset

def build_sales():
    """50,000 records; the six fields are drawn in this exact order per record."""
    rng = SplitMix64(SALES_SEED)
    sales = []
    for i in range(1, SALES_COUNT + 1):
        sales.append({
            "id": i,
            "region": rng.below(8),
            "category": rng.below(50),
            "customer_tier": rng.below(4),
            "amount_cents": 100 + rng.below(100_000),
            "quantity": 1 + rng.below(10),
            "day": rng.below(365),
        })
    return sales


def aggregate(sales, region: int, min_amount: int):
    groups = {}
    for s in sales:
        if s["region"] == region and s["amount_cents"] >= min_amount:
            g = groups.get(s["category"])
            if g is None:
                groups[s["category"]] = g = {"count": 0, "sum": 0, "min": s["amount_cents"],
                                             "max": s["amount_cents"], "qty": 0}
            g["count"] += 1
            g["sum"] += s["amount_cents"]
            g["min"] = min(g["min"], s["amount_cents"])
            g["max"] = max(g["max"], s["amount_cents"])
            g["qty"] += s["quantity"]
    rows = [{
        "category": c,
        "count": g["count"],
        "sum_amount": g["sum"],
        "min_amount": g["min"],
        "max_amount": g["max"],
        "avg_amount": g["sum"] // g["count"],
        "total_quantity": g["qty"],
    } for c, g in groups.items()]
    rows.sort(key=lambda r: (-r["sum_amount"], r["category"]))
    return {"region": region, "min_amount": min_amount, "groups": rows[:10]}


# ---------------------------------------------------------------- test 7: transform

CATEGORIES = ["Electronics", "Home", "Garden", "Kitchen", "Books", "Toys",
              "Sports", "Beauty", "Office", "Outdoor", "Audio", "Pets"]
ADJECTIVES = ["Classic", "SMART", "ultra", "Compact", "Eco", "Pro", "Deluxe", "mini", "Rugged", "Vintage"]
NOUNS = ["Widget", "Lamp", "Chair", "Kettle", "Speaker", "Backpack", "Monitor", "Jacket", "Blender", "Router"]
GAPS = [" ", "  ", "   ", "\t", " \t "]
ATTRIBUTES = {
    "color": ["red", "green", "blue", "black", "white", "silver"],
    "size": ["XS", "S", "M", "L", "XL"],
    "material": ["steel", "oak", "cotton", "aluminium", "recycled plastic"],
    "origin": ["DE", "US", "JP", "VN", "MX", "PL"],
    "warranty": ["none", "1y", "2y", "5y"],
}


def generate_transform_payloads(count: int = 200, items: int = 45):
    """Deterministic POST /transform request bodies (k6/data/transform/)."""
    rng = SplitMix64(TRANSFORM_SEED)
    pick = lambda xs: xs[rng.below(len(xs))]
    payloads = []
    for p in range(1, count + 1):
        lines = []
        for line_id in range(1, items + 1):
            pid = 1 + rng.below(100_000)
            name = (pick(GAPS) + pick(ADJECTIVES) + pick(GAPS) + pick(NOUNS)
                    + pick(GAPS) + "No." + str(pid) + pick(GAPS))
            attrs = {}
            for key in ATTRIBUTES:
                if rng.below(4) != 0:  # each attribute present with p = 3/4
                    attrs[key] = pick(ATTRIBUTES[key])
            discounts = []
            for _ in range(rng.below(3)):  # 0..2 discounts
                if rng.below(2) == 0:
                    discounts.append({"kind": "percent", "value": 5 + rng.below(46)})    # 5..50 %
                else:
                    discounts.append({"kind": "fixed", "value": 50 + rng.below(1951)})   # 0.50..20.00
            lines.append({
                "line_id": line_id,
                "product": {"id": pid, "name": name, "category": pick(CATEGORIES), "attributes": attrs},
                "pricing": {"unit": 99 + rng.below(99_901), "currency": "USD", "discounts": discounts},
                "quantity": 1 + rng.below(10),
            })
        payloads.append({"cart_id": f"cart-{p:04d}", "currency": "USD", "items": lines})
    return payloads


def normalize_name(name: str) -> str:
    """Trim, lowercase, collapse runs of ASCII whitespace (space, \\t, \\n, \\r) to one space."""
    return " ".join(name.split()).lower()


def discounted_unit(pricing) -> int:
    unit = pricing["unit"]
    for d in pricing["discounts"]:
        if d["kind"] == "percent":
            unit -= unit * d["value"] // 100
        else:  # fixed
            unit = max(0, unit - d["value"])
    return unit


def transform(payload):
    groups = {}
    total = 0
    for item in payload["items"]:
        line_total = discounted_unit(item["pricing"]) * item["quantity"]
        total += line_total
        g = groups.setdefault(item["product"]["category"], {"count": 0, "subtotal": 0, "items": []})
        g["count"] += 1
        g["subtotal"] += line_total
        g["items"].append({
            "line_id": item["line_id"],
            "product_id": item["product"]["id"],
            "name": normalize_name(item["product"]["name"]),
            "total_cents": line_total,
        })
    categories = []
    for cat, g in groups.items():
        top = sorted(g["items"], key=lambda i: (-i["total_cents"], i["line_id"]))[:3]
        categories.append({"category": cat, "count": g["count"], "subtotal_cents": g["subtotal"], "top_items": top})
    categories.sort(key=lambda c: (-c["subtotal_cents"], c["category"]))
    return {
        "cart_id": payload["cart_id"],
        "currency": payload["currency"],
        "item_count": len(payload["items"]),
        "total_cents": total,
        "categories": categories,
    }


# ---------------------------------------------------------------- test 6: downstream mock

def downstream_expected(product_id: int):
    return {"product_id": product_id, "available": (product_id * 7919) % 1000, "source": "downstream"}
