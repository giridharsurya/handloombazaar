from api.shops import ShopUpdateRequest
from db.db_models import shop


def test_shop_model_has_description_column():
    assert "description" in shop.__table__.columns
    assert shop.__table__.columns["description"].type.__class__.__name__ == "String"


def test_shop_update_request_accepts_description():
    payload = ShopUpdateRequest(description="A handcrafted heritage store")
    assert payload.description == "A handcrafted heritage store"


def test_shop_model_has_about_content_column():
    assert "about_content" in shop.__table__.columns
    assert shop.__table__.columns["about_content"].type.length == 10000


def test_shop_update_request_accepts_about_content():
    payload = ShopUpdateRequest(about_content="Our family has woven Mangalagiri cotton for generations.")
    assert payload.about_content.startswith("Our family")
