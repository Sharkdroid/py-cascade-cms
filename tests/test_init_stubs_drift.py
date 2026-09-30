"""The typing-only __init__ stubs must cover every model field."""

import ast
from pathlib import Path

import cascade_cms.cmstypes as ct


def _stub_kwonly_sets(cls_name: str) -> list[set[str]]:
    tree = ast.parse(Path(ct.__file__).read_text())
    cls = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == cls_name
    )
    found: list[set[str]] = []
    for node in cls.body:
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(
            node.test
        ):
            for fn in node.body:
                if (
                    isinstance(fn, ast.FunctionDef)
                    and fn.name == "__init__"
                    and fn.args.kwonlyargs
                ):
                    found.append({a.arg for a in fn.args.kwonlyargs})
    return found


def test_identifier_type_stubs_cover_both_spellings() -> None:
    fields = ct.IdentifierType.model_fields
    by_name = set(fields)
    by_alias = {
        str(f.validation_alias or name) for name, f in fields.items()
    }
    stubs = _stub_kwonly_sets("IdentifierType")
    assert by_name in stubs, "field-name overload is out of date"
    assert by_alias in stubs, "key-name (alias) overload is out of date"


def test_new_asset_stub_covers_every_field() -> None:
    stubs = _stub_kwonly_sets("NewAsset")
    assert stubs, "NewAsset has no typing-only __init__"
    assert set(ct.NewAsset.model_fields) <= stubs[0]


def test_identifier_type_spellings_are_equal() -> None:
    uid = "8b320f55ac1001062545a6d2562cee4b"
    a = ct.IdentifierType(id=uid, type="page")
    b = ct.IdentifierType(identifier=uid, asset_type="page")
    assert a == b
    assert a.model_dump(by_alias=True) == b.model_dump(by_alias=True)


def test_new_asset_extra_is_kept_in_the_dump() -> None:
    asset = ct.NewAsset(
        name="n",
        asset_type="page",
        site_name="s",
        parent_folder_path="/",
        metadata={"title": "t"},
    )
    dumped = asset.model_dump(by_alias=True)
    assert dumped["asset"]["page"]["metadata"] == {"title": "t"}
