"""Memory footprint of file `Asset`s: byte array converted to `bytes` vs left as `list[int]`.

Offline and deterministic: 202 synthetic file payloads (the size of the
`www.csi.edu` `_files/pdf/institutional-effectiveness` folder on apptest)
with PDF-like signed byte arrays. Run with `pytest -s tests/test_file_data_memory.py`
to see the report.
"""

import gc
import json
import random
import tracemalloc

from cascade_cms.cmstypes import Asset, asset_adapter

ASSET_COUNT = 202
SEED = 20260610


def build_payloads() -> list[bytes]:
    rng = random.Random(SEED)
    payloads = []
    for i in range(ASSET_COUNT):
        size = rng.randint(20_000, 150_000)
        signed = [b - 256 if b > 127 else b for b in rng.randbytes(size)]
        payloads.append(
            json.dumps({"asset": {"file": {"id": f"id{i}", "name": f"{i}.pdf", "data": signed}}}).encode()
        )
    return payloads


def held_bytes(payloads: list[bytes], load) -> tuple[int, int]:
    """(traced bytes held by the parsed assets, total data elements)."""
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        assets = [load(p) for p in payloads]
        gc.collect()
        held = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    return held, sum(len(a._data["data"]) for a in assets)


def test_converted_asset_footprint_is_smaller(capsys):
    payloads = build_payloads()
    plain, elements = held_bytes(payloads, lambda p: Asset(json.loads(p)))
    converted, same_elements = held_bytes(payloads, asset_adapter.validate_json)
    assert elements == same_elements

    ratio = plain / converted
    report = (
        f"\nfile Asset memory ({ASSET_COUNT} assets, {elements:,} data elements)\n"
        f"{'mode':<22}{'held bytes':>16}{'bytes/element':>16}\n"
        f"{'no conversion (list)':<22}{plain:>16,}{plain / elements:>16.2f}\n"
        f"{'converted (bytes)':<22}{converted:>16,}{converted / elements:>16.2f}\n"
        f"reduction: {ratio:.1f}x smaller ({(1 - converted / plain) * 100:.1f}% saved)\n"
    )
    with capsys.disabled():
        print(report)

    assert converted < plain / 4


def test_round_trip_restores_identical_list():
    payload = build_payloads()[0]
    original = json.loads(payload)["asset"]["file"]["data"]
    asset = asset_adapter.validate_json(payload)
    assert isinstance(asset._data["data"], bytes)
    assert json.loads(asset_adapter.dump_json(asset))["asset"]["file"]["data"] == original
