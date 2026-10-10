#!/usr/bin/env python3
"""Writes the 200 deterministic POST /transform bodies to k6/data/transform/NNNN.json."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from reference import generate_transform_payloads  # noqa: E402

out = pathlib.Path(__file__).resolve().parents[2] / "k6" / "data" / "transform"
out.mkdir(parents=True, exist_ok=True)
sizes = []
for i, payload in enumerate(generate_transform_payloads(), start=1):
    body = json.dumps(payload, separators=(",", ":"))
    (out / f"{i:04d}.json").write_text(body)
    sizes.append(len(body))
print(f"wrote {len(sizes)} payloads to {out}, {min(sizes)}..{max(sizes)} bytes, avg {sum(sizes)//len(sizes)}")
