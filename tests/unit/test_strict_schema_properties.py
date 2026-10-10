from itertools import count
from string import ascii_lowercase
from typing import Literal

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ConfigDict, create_model

from backend.domain.ports import strict_schema

MODEL_NUMBERS = count()
# field names that are also words of the schema language, beside ordinary ones
FIELD_NAMES = st.sampled_from(["title", "default", "properties", "type", "required", "items"]) | st.text(
    alphabet=ascii_lowercase, min_size=1, max_size=5
).map(lambda name: f"f_{name}")
SCALARS = st.sampled_from([str, int, float, bool]) | st.lists(st.text(alphabet=ascii_lowercase, min_size=1, max_size=4), min_size=1, max_size=3).map(
    lambda values: Literal[*values]
)


# a field that must be given, or one that may be left out and is then null
def fields(annotations):
    return st.tuples(annotations, st.booleans()).map(
        lambda drawn: (drawn[0] | None, None) if drawn[1] else (drawn[0], ...)
    )


# a model with any fields, which by its own configuration forbids, ignores or allows properties it does not name
def models_of(annotations):
    return st.tuples(st.dictionaries(FIELD_NAMES, fields(annotations), max_size=4), st.sampled_from(["forbid", "ignore", "allow"])).map(
        lambda drawn: create_model(f"Generated{next(MODEL_NUMBERS)}", __config__=ConfigDict(extra=drawn[1]), **drawn[0])
    )


# what the tool arguments and the draft are built from: scalars, closed lists of words, nulls, lists and nested models
ANNOTATIONS = st.recursive(
    SCALARS,
    lambda inner: st.one_of(inner.map(lambda annotation: list[annotation]), inner.map(lambda annotation: annotation | None), models_of(inner)),
    max_leaves=6,
)
MODELS = models_of(ANNOTATIONS)


# a model class in name only, whose schema is the one given
class Described:
    def __init__(self, schema):
        self.schema = schema

    def model_json_schema(self):
        return self.schema


# every schema in the tree, without mistaking a map of property or definition names for one
def schemas_in(node, is_map_of_names=False):
    if isinstance(node, list):
        for item in node:
            yield from schemas_in(item)
    elif isinstance(node, dict) and is_map_of_names:
        for named in node.values():
            yield from schemas_in(named)
    elif isinstance(node, dict):
        yield node
        for key, value in node.items():
            yield from schemas_in(value, is_map_of_names=key in ("properties", "$defs"))


@given(MODELS)
def test_every_object_of_a_strict_schema_forbids_extra_properties_and_requires_each_of_its_own(model):
    objects = [schema for schema in schemas_in(strict_schema(model)) if schema.get("type") == "object"]

    assert objects
    for schema in objects:
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])


@given(MODELS)
def test_a_strict_schema_keeps_every_field_under_its_name_and_carries_no_title_and_no_default(model):
    strict = strict_schema(model)

    assert list(strict["properties"]) == list(model.model_fields)
    assert all("title" not in schema and "default" not in schema for schema in schemas_in(strict))


@given(MODELS)
def test_every_reference_of_a_strict_schema_points_at_a_definition_it_kept(model):
    strict = strict_schema(model)
    references = {schema["$ref"] for schema in schemas_in(strict) if "$ref" in schema}

    assert references == {f"#/$defs/{name}" for name in strict.get("$defs", {})}


@given(MODELS)
def test_a_schema_that_is_already_strict_is_returned_unchanged(model):
    strict = strict_schema(model)

    assert strict_schema(Described(strict)) == strict
