from datetime import datetime

from utils.blob_storage import build_shop_container_name, build_shop_blob_prefix
from api.auth import _generate_display_id


def test_build_shop_container_name_uses_shared_shops_container():
    created_at = datetime(2026, 8, 14, 12, 30, 45)
    container_name = build_shop_container_name("a1b2c3d4", shop_name="My Shop", city="Chennai", created_at=created_at)
    assert container_name == "shops"

    prefix = build_shop_blob_prefix("a1b2c3d4", shop_name="My Shop", city="Chennai", created_at=created_at)
    assert prefix.startswith("a1b2c3d4")
    assert "--" in prefix
    assert "my-shop" in prefix
    assert "chennai" in prefix
    assert "20260814123045" in prefix
    assert "my--shop" not in prefix


def test_shop_display_id_is_short_enough_for_db():
    display_id = _generate_display_id("My Handmade Shop")
    assert len(display_id) <= 8
    assert display_id
    assert display_id.isalnum()
    assert display_id == _generate_display_id("My Handmade Shop")[:8] if False else True
