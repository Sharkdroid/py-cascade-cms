"""Tests for `cascade_cms.utils.identifiers` (`to_identifier`, `to_identifiers`)."""

import uuid

import pytest

from cascade_cms.cmstypes import IdentifierType
from cascade_cms.utils import to_identifier, to_identifiers

ID_ONE = "8b320f55ac1001062545a6d2562cee4b"
ID_TWO = "9c431066bd21120736f6b7e3673dff5c"
SITE_ID = "1a2b3c4d5e6f70819293a4b5c6d7e8f9"
SENTINEL = "SENTINEL-VALUE-XYZ"


def child(id_: str = ID_ONE, type_: str = "page", **extra):
    return {
        "id": id_,
        "type": type_,
        "path": {"path": "dir/item", "siteId": SITE_ID},
        "recycled": False,
        **extra,
    }


class TestAccepted:
    def test_real_children_entry(self):
        (ident,) = to_identifiers([child()])
        assert isinstance(ident, IdentifierType)
        assert ident.identifier == uuid.UUID(ID_ONE)
        assert ident.asset_type == "page"
        assert ident.path is not None
        assert ident.path.path == "dir/item"

    def test_dashed_uuid(self):
        ident = to_identifier({"id": str(uuid.UUID(ID_ONE)), "type": "page"})
        assert ident.identifier == uuid.UUID(ID_ONE)

    def test_snake_case_keys(self):
        ident = to_identifier({"identifier": ID_ONE, "asset_type": "folder"})
        assert ident.asset_type == "folder"

    def test_import_path(self):
        from cascade_cms.utils import to_identifier as ti
        from cascade_cms.utils import to_identifiers as tis

        assert ti is to_identifier
        assert tis is to_identifiers


class TestFiltering:
    def test_recycled_dropped_by_default(self):
        raw = [child(ID_ONE, recycled=True), child(ID_TWO)]
        assert [i.identifier.hex for i in to_identifiers(raw)] == [ID_TWO]

    def test_recycled_kept_with_flag(self):
        raw = [child(ID_ONE, recycled=True), child(ID_TWO)]
        found = to_identifiers(raw, include_recycled=True)
        assert [i.identifier.hex for i in found] == [ID_ONE, ID_TWO]

    def test_recycled_none_and_false_kept(self):
        raw = [
            {"id": ID_ONE, "type": "page", "recycled": None},
            {"id": ID_TWO, "type": "page", "recycled": False},
        ]
        assert len(to_identifiers(raw)) == 2

    def test_to_identifier_does_not_filter_recycled(self):
        assert to_identifier(child(recycled=True)).recycled is True

    def test_none_and_empty(self):
        assert to_identifiers(None) == []
        assert to_identifiers([]) == []

    def test_order_preserved(self):
        raw = [child(ID_TWO, "file"), child(ID_ONE, "page"), child(ID_TWO, "folder")]
        found = to_identifiers(raw)
        assert [(i.identifier.hex, i.asset_type) for i in found] == [
            (ID_TWO, "file"),
            (ID_ONE, "page"),
            (ID_TWO, "folder"),
        ]

    def test_accepts_generator(self):
        assert len(to_identifiers(child(i) for i in (ID_ONE, ID_TWO))) == 2


class TestErrors:
    @pytest.mark.parametrize(
        "entry, field",
        [
            ({"id": ID_ONE, "type": "page", "name": SENTINEL}, "name"),
            ({"id": ID_ONE, "type": "page", "path": f"{SENTINEL}/x"}, "path"),
            ({"type": "page", "path": {"path": "a", "siteName": "s"}}, "id"),
            ({"id": ID_ONE, "type": SENTINEL}, "type"),
        ],
    )
    def test_bad_entry_names_index_and_field(self, entry, field):
        with pytest.raises(ValueError) as exc:
            to_identifiers([child(), entry])
        message = str(exc.value)
        assert message.startswith("entry 1: ")
        assert field in message
        assert SENTINEL not in message
        assert len(message) < 160

    def test_non_mapping_entry(self):
        with pytest.raises(ValueError, match=r"entry 0: expected a mapping"):
            to_identifiers(["not-a-dict"])  # type: ignore[list-item]

    def test_to_identifier_non_mapping(self):
        with pytest.raises(ValueError, match="expected a mapping, got int"):
            to_identifier(3)  # type: ignore[arg-type]

    def test_string_path_message_explains_shape(self):
        with pytest.raises(ValueError, match="'siteName'/'siteId'"):
            to_identifier({"id": ID_ONE, "type": "page", "path": "a/b"})

    def test_unknown_type_message_is_short(self):
        with pytest.raises(ValueError) as exc:
            to_identifier({"id": ID_ONE, "type": "nonsense"})
        assert str(exc.value) == "type: not a known asset type"

    def test_no_pydantic_prefix(self):
        with pytest.raises(ValueError) as exc:
            to_identifier({"id": ID_ONE, "type": "page", "name": "x"})
        assert "Value error, " not in str(exc.value)

    def test_original_exception_chained(self):
        with pytest.raises(ValueError) as exc:
            to_identifiers([{"type": "page"}])
        assert exc.value.__cause__ is not None
        assert exc.value.__cause__.__cause__ is not None
