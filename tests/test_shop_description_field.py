from api.shops import ShopUpdateRequest
from db.db_models import shop


def test_shop_model_has_description_column():
    assert "description" in shop.__table__.columns
    assert shop.__table__.columns["description"].type.__class__.__name__ == "String"


def test_shop_update_request_accepts_description():
    payload = ShopUpdateRequest(description="A handcrafted heritage store")
    assert payload.description == "A handcrafted heritage store"
