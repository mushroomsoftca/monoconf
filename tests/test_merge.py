"""Deep merge and provenance: the pure core, tested exhaustively."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from monoconf import MonoconfError, Origin, SourceKind
from monoconf.merge import Layer, Values, leaves, merge, origins_for

DEFAULT = Origin(SourceKind.DEFAULT)
SYSTEM = Origin(SourceKind.SYSTEM_FILE, path=Path("/etc/myapp.toml"))
DROPIN = Origin(SourceKind.SYSTEM_DROPIN, path=Path("/etc/myapp.d/20-site.toml"))
ENV = Origin(SourceKind.ENV, detail="MYAPP_PORT")
CLI = Origin(SourceKind.CLI, detail="--port")


def layer(values: Values, origin: Origin) -> Layer:
    return values, origins_for(values, origin)


def test_describe_names_the_narrowest_thing_it_can() -> None:
    assert SYSTEM.describe() == "/etc/myapp.toml"
    assert ENV.describe() == "MYAPP_PORT"
    assert DEFAULT.describe() == "default"


def test_leaves_walks_nested_values() -> None:
    values = {"port": 8080, "database": {"host": "db", "replicas": ["a", "b"]}}
    assert dict(leaves(values)) == {
        "port": 8080,
        "database.host": "db",
        # A list is a value, not structure.
        "database.replicas": ["a", "b"],
    }


def test_later_layers_win_per_leaf() -> None:
    values, origins = merge(
        [
            layer({"port": 8080, "host": "localhost"}, DEFAULT),
            layer({"port": 9090}, SYSTEM),
        ]
    )
    assert values == {"port": 9090, "host": "localhost"}
    assert origins["port"] is SYSTEM
    assert origins["host"] is DEFAULT


def test_deep_merge_keeps_sibling_leaves() -> None:
    values, origins = merge(
        [
            layer({"database": {"host": "localhost", "port": 5432}}, DEFAULT),
            layer({"database": {"host": "db.internal"}}, SYSTEM),
            layer({"database": {"port": 6000}}, DROPIN),
        ]
    )
    assert values == {"database": {"host": "db.internal", "port": 6000}}
    assert origins["database.host"] is SYSTEM
    assert origins["database.port"] is DROPIN


def test_lists_replace_wholesale() -> None:
    values, origins = merge(
        [
            layer({"tags": ["a", "b", "c"]}, DEFAULT),
            layer({"tags": ["d"]}, DROPIN),
        ]
    )
    assert values == {"tags": ["d"]}
    assert origins["tags"] is DROPIN


def test_an_absent_key_contributes_nothing() -> None:
    # Sparseness is the whole reason sources report what they set rather than a
    # full tree: a None from a later layer would otherwise clobber a file value.
    values, origins = merge(
        [
            layer({"proxy": "http://proxy:3128"}, SYSTEM),
            layer({"port": 9090}, ENV),
        ]
    )
    assert values == {"proxy": "http://proxy:3128", "port": 9090}
    assert origins["proxy"] is SYSTEM


def test_an_explicit_none_does_overwrite() -> None:
    # Absent is not null, but null is null: an operator clearing a value with an
    # empty string is setting it, and the origin says who did.
    values, origins = merge(
        [
            layer({"proxy": "http://proxy:3128"}, SYSTEM),
            layer({"proxy": None}, CLI),
        ]
    )
    assert values == {"proxy": None}
    assert origins["proxy"] is CLI


def test_empty_branches_never_materialise() -> None:
    values, origins = merge(
        [
            layer({"port": 8080}, DEFAULT),
            ({"database": {}, "nested": {"deeper": {}}}, {}),
        ]
    )
    assert values == {"port": 8080}
    assert set(origins) == {"port"}


def test_merging_nothing_yields_nothing() -> None:
    assert merge([]) == ({}, {})


def test_a_value_without_an_origin_is_an_error() -> None:
    with pytest.raises(MonoconfError, match="without an origin"):
        merge([({"port": 9090}, {})])


def test_a_nested_value_without_an_origin_is_an_error() -> None:
    with pytest.raises(MonoconfError, match=r"'database\.host' without an origin"):
        merge([({"database": {"host": "db"}}, {})])


def test_the_full_chain_resolves_to_the_top_layer() -> None:
    chain = [
        layer({"port": 1}, DEFAULT),
        layer({"port": 2}, SYSTEM),
        layer({"port": 3}, DROPIN),
        layer({"port": 4}, ENV),
        layer({"port": 5}, CLI),
    ]
    for cut in range(len(chain), 0, -1):
        values, origins = merge(chain[:cut])
        assert values == {"port": cut}
        assert origins["port"] is chain[cut - 1][1]["port"]


# A fixed schema shape: sources set subsets of the same paths, which is what they
# really do. Nothing generates a path that is a prefix of another, because a field
# is either a leaf or a branch and never both.
PATHS = ["port", "host", "database.host", "database.port", "tags", "a.b.c"]
ORIGINS = [DEFAULT, SYSTEM, DROPIN, ENV, CLI]


def _nest(path: str, value: Any) -> Values:
    head, _, rest = path.partition(".")
    return {head: _nest(rest, value) if rest else value}


def _deep_update(target: Values, extra: Values) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _build_layer(flat: dict[str, Any], origin: Origin) -> Layer:
    values: Values = {}
    for path, value in flat.items():
        _deep_update(values, _nest(path, value))
    return layer(values, origin)


_SCALARS = st.one_of(st.integers(max_value=99), st.text(max_size=3), st.booleans())
_VALUES = st.one_of(_SCALARS, st.lists(st.text(max_size=2), max_size=2))
_LAYER = st.builds(
    _build_layer,
    st.dictionaries(st.sampled_from(PATHS), _VALUES, max_size=3),
    st.sampled_from(ORIGINS),
)
layers = st.lists(_LAYER, max_size=4)


@given(layers)
def test_merging_is_associative(built: list[Layer]) -> None:
    # Folding left to right must equal folding any adjacent pair first, or the
    # order a resolver happens to group layers in would change the result.
    expected = merge(built)
    for cut in range(len(built) + 1):
        head, tail = built[:cut], built[cut:]
        assert merge([merge(head), *tail]) == expected
        assert merge([*head, merge(tail)]) == expected


@given(layers)
def test_every_leaf_has_exactly_one_origin(built: list[Layer]) -> None:
    values, origins = merge(built)
    assert {path for path, _ in leaves(values)} == set(origins)


@given(layers)
def test_the_winning_origin_is_the_last_layer_that_set_the_leaf(
    built: list[Layer],
) -> None:
    values, origins = merge(built)
    for path in {path for path, _ in leaves(values)}:
        last = [origin[path] for _, origin in built if path in origin][-1]
        assert origins[path] == last
