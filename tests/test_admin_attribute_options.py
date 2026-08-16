from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.admin import AttributeOptionCreateRequest, attribute_definition, create_attribute_option


class FakeQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result

    def all(self):
        if isinstance(self.result, list):
            return self.result
        return [] if self.result is None else [self.result]


class FakeSession:
    def __init__(self, attribute_row=None, option_rows=None):
        self.attribute_row = attribute_row
        self.option_rows = option_rows or []
        self.added = []

    def query(self, model):
        if model is attribute_definition:
            return FakeQuery(self.attribute_row)
        if model is __import__("db.db_models", fromlist=["attribute_option"]).attribute_option:
            return FakeQuery(self.option_rows)
        return FakeQuery(None)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def test_create_attribute_option_for_existing_attribute():
    session = FakeSession(attribute_row=SimpleNamespace(id=5))

    response = create_attribute_option(
        attribute_id=5,
        payload=AttributeOptionCreateRequest(option_value="Teal"),
        session=session,
    )

    assert response["message"] == "Option created successfully"
    assert session.added[0].attribute_definition_id == 5
    assert session.added[0].option_value == "Teal"


def test_create_attribute_option_rejects_unknown_attribute():
    session = FakeSession(attribute_row=None)

    with pytest.raises(HTTPException, match="Attribute not found"):
        create_attribute_option(
            attribute_id=999,
            payload=AttributeOptionCreateRequest(option_value="Blue"),
            session=session,
        )
