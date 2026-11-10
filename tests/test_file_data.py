"""Java `byte[]` (signed int list) <-> `bytes` conversion for file `data`."""

import json
import random

import pytest

from cascade_cms.cmstypes import (
    Asset,
    NewAsset,
    asset_adapter,
    bytes_from_cascade,
    bytes_to_cascade,
    is_java_byte_array,
    serialize_payload,
)


def file_json(data) -> bytes:
    return json.dumps({"asset": {"file": {"id": "abc", "name": "x.bin", "data": data}}}).encode()


def test_signed_to_bytes():
    assert bytes_from_cascade([-119, 80, 78]) == b"\x89PN"


def test_all_byte_values_round_trip():
    raw = bytes(range(256))
    signed = bytes_to_cascade(raw)
    assert min(signed) == -128 and max(signed) == 127
    assert bytes_from_cascade(signed) == raw


def test_large_array_round_trip():
    rng = random.Random(1)
    raw = rng.randbytes(1_000_000)
    assert bytes_from_cascade(bytes_to_cascade(raw)) == raw


@pytest.mark.parametrize("value", ["%PDF-1.4", None, [], [1, 2, 300], [-129], [True, False], [1, "a"], [1.0], b"raw"])
def test_not_a_byte_array(value):
    assert not is_java_byte_array(value)


def test_read_converts_byte_array_only():
    asset = asset_adapter.validate_json(file_json([37, 80, -10, -28]))
    assert asset.get("data") == b"%P\xf6\xe4"


@pytest.mark.parametrize("value", ["plain text", [], [1, 999], None])
def test_read_leaves_other_types(value):
    asset = asset_adapter.validate_json(file_json(value))
    assert asset.get("data") == value
    assert json.loads(asset_adapter.dump_json(asset))["asset"]["file"]["data"] == value


def test_edit_payload_encodes_and_keeps_held_bytes():
    signed = [-119, 80, 78, 71, 0, 127, -128]
    asset = asset_adapter.validate_json(file_json(signed))
    body = json.loads(serialize_payload(asset))
    assert body["asset"]["file"]["data"] == signed
    assert asset.get("data") == bytes_from_cascade(signed)  # not mutated


def test_edit_with_replaced_bytes():
    asset = asset_adapter.validate_json(file_json([1, 2]))
    asset.data = b"\xff\x00"
    assert json.loads(serialize_payload(asset))["asset"]["file"]["data"] == [-1, 0]


def test_edit_list_and_str_pass_through():
    for value in ([1, -2], "text"):
        asset = Asset({"asset": {"file": {"id": "abc", "data": value}}})
        assert json.loads(serialize_payload(asset))["asset"]["file"]["data"] == value


def test_create_encodes_bytes():
    new = NewAsset(name="x.bin", asset_type="file", site_name="s", parent_folder_path="/", data=b"\x89PN\xff")
    assert json.loads(serialize_payload(new))["asset"]["file"]["data"] == [-119, 80, 78, -1]


def test_create_leaves_str_and_list():
    for value in ("hello", [1, -2]):
        new = NewAsset(name="x.txt", asset_type="file", site_name="s", parent_folder_path="/", data=value)
        assert json.loads(serialize_payload(new))["asset"]["file"]["data"] == value


def test_create_does_not_mutate_model():
    new = NewAsset(name="x.bin", asset_type="file", site_name="s", parent_folder_path="/", data=b"\x01")
    serialize_payload(new)
    assert new.model_extra == {"data": b"\x01"}
