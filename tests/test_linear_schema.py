"""Valida las consultas/mutaciones de aipipe contra el esquema GraphQL oficial de Linear (opcional).

    curl -o /tmp/linear_schema.graphql https://raw.githubusercontent.com/linear/linear/refs/heads/master/packages/sdk/src/schema.graphql
    pip install graphql-core
    AIPIPE_LINEAR_SCHEMA=/tmp/linear_schema.graphql pytest tests/test_linear_schema.py

Util si Linear cambia su API: avisa antes de que el pipeline falle en produccion.
"""
import os
from pathlib import Path

import pytest

graphql = pytest.importorskip("graphql")
SCHEMA = os.environ.get("AIPIPE_LINEAR_SCHEMA")
pytestmark = pytest.mark.skipif(not SCHEMA or not Path(SCHEMA or "").exists(), reason="definir AIPIPE_LINEAR_SCHEMA")

from graphql import GraphQLInputObjectType, GraphQLList, GraphQLNonNull, build_schema, parse, validate  # noqa: E402

from aipipe import linear as L  # noqa: E402


@pytest.fixture(scope="module")
def schema():
    return build_schema(Path(SCHEMA).read_text())


@pytest.mark.parametrize("name", ["Q_READY", "Q_GET", "Q_VIEWER", "Q_STATE", "Q_ISSUE_LABELS", "Q_COMMENTS", "Q_STATES", "Q_LABELS", "M_UPDATE", "M_COMMENT", "M_LABEL"])
def test_operation_is_valid(schema, name):
    assert validate(schema, parse(getattr(L, name))) == []


def _check(value, t, path="$"):
    errs = []
    if isinstance(t, GraphQLNonNull):
        t = t.of_type
    if isinstance(t, GraphQLList):
        for i, v in enumerate(value if isinstance(value, list) else [value]):
            errs += _check(v, t.of_type, f"{path}[{i}]")
    elif isinstance(t, GraphQLInputObjectType):
        for k, v in value.items():
            if k not in t.fields:
                errs.append(f"{path}.{k} no existe en {t.name}")
            else:
                errs += _check(v, t.fields[k].type, f"{path}.{k}")
    return errs


@pytest.mark.parametrize(
    "type_name,value",
    [
        ("IssueFilter", {"labels": {"name": {"eq": "x"}}, "state": {"name": {"in": ["Todo"]}}, "team": {"key": {"eq": "E"}}}),
        ("IssueFilter", {"labels": {"name": {"eq": "x"}}, "creator": {"id": {"eq": "u"}}}),
        ("IssueUpdateInput", {"stateId": "x", "addedLabelIds": ["a"], "removedLabelIds": ["b"]}),
        ("CommentCreateInput", {"issueId": "x", "body": "b"}),
        ("IssueLabelCreateInput", {"name": "n", "teamId": "t"}),
    ],
)
def test_input_values_match_schema(schema, type_name, value):
    assert _check(value, schema.type_map[type_name]) == []


def test_validator_detects_errors(schema):  # control negativo
    assert _check({"labels": {"nombre": {"eq": "x"}}}, schema.type_map["IssueFilter"])
