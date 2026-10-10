# downstream

Mock "slow downstream" service for test 6 ([docs/02-test-spec.md](../docs/02-test-spec.md#6--slow-downstream-call-get-downstreamid)). Go `net/http`, standard library only.

| Item | Value |
|---|---|
| Endpoint | `GET /inventory/{id}` → after `DELAY_MS` (50) ms: `{"id": id, "warehouse": "wh-<id % 8>", "available": (id × 7919) % 1000}` |
| Port | `9000` (`LISTEN_ADDR`) |
| Health | `GET /health` → 200 |
| Host | db host, never the app host |

```bash
docker compose -f downstream/compose.yml up -d --build
curl -s localhost:9000/inventory/42
```
