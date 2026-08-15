from datetime import datetime
from types import SimpleNamespace
import uuid

import pytest

from api.admin import deactivate_shop, reactivate_shop
from api.collections import list_collections
from api.products import (
    _build_product_search_conditions,
    _ensure_required_attributes_selected,
    get_product_variants,
)
from api.shops import list_shops
from db.database import get_session
from db.db_models import (
    collection,
    collection_product,
    collection_shop,
    product,
    product_group,
    shop,
    shop_collection,
    user,
    UserRole,
)


@pytest.mark.parametrize(
    "search_text, expected_terms",
    [
        ("maheswari saree", ["maheswari", "saree"]),
        ("Maheswari", ["maheswari"]),
        ("   silk   saree  ", ["silk", "saree"]),
    ],
)
def test_build_product_search_conditions_uses_word_terms(search_text, expected_terms):
    conditions = _build_product_search_conditions(search_text)

    assert conditions[0] == " ".join(search_text.strip().split()).lower()
    assert conditions[1:] == expected_terms


def test_build_product_search_conditions_keeps_display_id_match():
    conditions = _build_product_search_conditions("PROD-123")

    assert conditions[0] == "prod-123"
    assert conditions[1:] == ["prod-123"]


def test_exact_name_match_takes_priority_before_word_search():
    exact_match, phrase_match, all_words_match, any_words_match = __import__("api.products", fromlist=["_build_product_search_filters"])._build_product_search_filters("shop 1 product 1")

    assert exact_match is not None
    assert phrase_match is not None
    assert all_words_match is not None
    assert any_words_match is not None

    exact_sql = str(exact_match)
    phrase_sql = str(phrase_match)
    all_words_sql = str(all_words_match)
    any_words_sql = str(any_words_match)

    assert "lower(products.name)" in exact_sql
    assert "lower(products.display_id)" in exact_sql
    assert "like" in phrase_sql.lower()
    assert "and" in all_words_sql.lower()
    assert "or" in any_words_sql.lower()


def test_ensure_required_attributes_selected_rejects_missing_required_definition():
    session = next(get_session())
    now = datetime.now()
    unique = uuid.uuid4().hex[:8]

    attrib = __import__("db.db_models", fromlist=["attribute_definition", "attribute_option"]).attribute_definition(
        attribute_name=f"Required Color {unique}",
        is_filterable=True,
        is_required=True,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(attrib)
    session.flush()

    option = __import__("db.db_models", fromlist=["attribute_option"]).attribute_option(
        attribute_definition_id=attrib.id,
        option_value=f"Red {unique}",
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(option)
    session.flush()

    session.query(__import__("db.db_models", fromlist=["attribute_definition"]).attribute_definition).filter(
        __import__("db.db_models", fromlist=["attribute_definition"]).attribute_definition.is_required.is_(True),
        __import__("db.db_models", fromlist=["attribute_definition"]).attribute_definition.id != attrib.id,
    ).update({"is_required": False}, synchronize_session=False)
    session.commit()

    with pytest.raises(Exception):
        _ensure_required_attributes_selected(session, {})

    _ensure_required_attributes_selected(session, {attrib.id: option.id})
    session.close()


def test_list_shops_only_returns_shops_with_active_products():
    session = next(get_session())
    now = datetime.now()
    unique = uuid.uuid4().hex[:8]

    owner_one = user(
        username=f"public_listing_owner_one_{unique}",
        email=f"public_listing_owner_one_{unique}@example.com",
        password_hash="hash",
        role=UserRole.SHOP_OWNER,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    owner_two = user(
        username=f"public_listing_owner_two_{unique}",
        email=f"public_listing_owner_two_{unique}@example.com",
        password_hash="hash",
        role=UserRole.SHOP_OWNER,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add_all([owner_one, owner_two])
    session.flush()

    shop_with_product = shop(
        owner_id=owner_one.id,
        display_id=uuid.uuid4().hex[:8],
        name=f"Visible Shop {unique}",
        year_established=2020,
        address="Main Street",
        city="Test City",
        phone_number="1234567890",
        email=f"visible_{unique}@example.com",
        website_url=None,
        shop_logo_url="/images/logo.jpg",
        youtube_url=None,
        instagram_url=None,
        facebook_url=None,
        created_at=now,
        updated_at=now,
        is_active=True,
        approved=True,
    )
    shop_without_product = shop(
        owner_id=owner_two.id,
        display_id=uuid.uuid4().hex[:8],
        name=f"Hidden Shop {unique}",
        year_established=2021,
        address="Second Street",
        city="Test City",
        phone_number="1234567891",
        email=f"hidden_{unique}@example.com",
        website_url=None,
        shop_logo_url="/images/logo.jpg",
        youtube_url=None,
        instagram_url=None,
        facebook_url=None,
        created_at=now,
        updated_at=now,
        is_active=True,
        approved=True,
    )
    session.add_all([shop_with_product, shop_without_product])
    session.flush()

    session.add(
        product(
            display_id=uuid.uuid4().hex[:8],
            shop_id=shop_with_product.id,
            name=f"Visible Product {unique}",
            price=150,
            stock_quantity=12,
            created_at=now,
            updated_at=now,
            is_active=True,
        )
    )
    session.add(
        product(
            display_id=uuid.uuid4().hex[:8],
            shop_id=shop_without_product.id,
            name=f"Hidden Product {unique}",
            price=200,
            stock_quantity=5,
            created_at=now,
            updated_at=now,
            is_active=False,
        )
    )
    session.commit()

    response = list_shops(page=1, page_size=20, sort_by="newest", session=session)

    names = [item["name"] for item in response["items"]]
    assert shop_with_product.name in names
    assert shop_without_product.name not in names
    session.close()


def test_get_product_variants_for_anonymous_user_does_not_error():
    session = next(get_session())
    now = datetime.now()
    unique = uuid.uuid4().hex[:8]

    owner = user(
        username=f"variant_owner_{unique}",
        email=f"variant_owner_{unique}@example.com",
        password_hash="hash",
        role=UserRole.SHOP_OWNER,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(owner)
    session.flush()

    shop_row = shop(
        owner_id=owner.id,
        display_id=uuid.uuid4().hex[:8],
        name=f"Variant Shop {unique}",
        year_established=2020,
        address="Main Street",
        city="Test City",
        phone_number="1234567890",
        email=f"variant_shop_{unique}@example.com",
        website_url=None,
        shop_logo_url="/images/logo.jpg",
        youtube_url=None,
        instagram_url=None,
        facebook_url=None,
        created_at=now,
        updated_at=now,
        is_active=True,
        approved=True,
    )
    session.add(shop_row)
    session.flush()

    product_group_row = product_group(
        shop_id=shop_row.id,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(product_group_row)
    session.flush()

    target_product = product(
        display_id=uuid.uuid4().hex[:8],
        shop_id=shop_row.id,
        product_group_id=product_group_row.id,
        name=f"Variant Product {unique}",
        price=150,
        stock_quantity=12,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(target_product)
    session.flush()

    variant_product = product(
        display_id=uuid.uuid4().hex[:8],
        shop_id=shop_row.id,
        product_group_id=product_group_row.id,
        name=f"Variant Product {unique} Alt",
        price=180,
        stock_quantity=4,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(variant_product)
    session.commit()

    try:
        response = get_product_variants(
            SimpleNamespace(state=SimpleNamespace(current_user=None)),
            target_product.display_id,
            session,
        )
        assert response.success is True
        assert isinstance(response.data, list)
        assert len(response.data) >= 1
        assert all(item.is_active is None or isinstance(item.is_active, bool) for item in response.data)
    finally:
        session.close()


def test_admin_deactivate_shop_inactivates_products_and_collections():
    session = next(get_session())
    now = datetime.now()
    unique = uuid.uuid4().hex[:8]

    owner = user(
        username=f"shopowner_{unique}",
        email=f"shopowner_{unique}@example.com",
        password_hash="hash",
        role=UserRole.SHOP_OWNER,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(owner)
    session.flush()

    target_shop = shop(
        owner_id=owner.id,
        display_id=uuid.uuid4().hex[:8],
        name=f"Inactive Shop {unique}",
        year_established=2020,
        address="Main Street",
        city="Test City",
        phone_number="1234567890",
        email=f"shop_{unique}@example.com",
        website_url=None,
        shop_logo_url="/images/logo.jpg",
        youtube_url=None,
        instagram_url=None,
        facebook_url=None,
        created_at=now,
        updated_at=now,
        is_active=True,
        approved=True,
    )
    session.add(target_shop)
    session.flush()

    product_one = product(
        display_id=uuid.uuid4().hex[:8],
        shop_id=target_shop.id,
        name=f"Shop Product {unique} 1",
        price=100,
        stock_quantity=5,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    product_two = product(
        display_id=uuid.uuid4().hex[:8],
        shop_id=target_shop.id,
        name=f"Shop Product {unique} 2",
        price=200,
        stock_quantity=3,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add_all([product_one, product_two])
    session.flush()

    system_collection = collection(
        name=f"System Collection {unique}",
        description="Test collection",
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(system_collection)
    session.flush()

    session.add(
        collection_shop(
            collection_id=system_collection.id,
            shop_id=target_shop.id,
            shop_display_id=target_shop.display_id,
            created_at=now,
            updated_at=now,
        )
    )

    shop_collection_row = shop_collection(
        shop_id=target_shop.id,
        collection_id=system_collection.id,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(shop_collection_row)
    session.commit()

    deactivate_shop(target_shop.id, session)
    session.refresh(target_shop)
    session.refresh(product_one)
    session.refresh(product_two)
    session.refresh(system_collection)
    session.refresh(shop_collection_row)

    assert target_shop.is_active is False
    assert product_one.is_active is False
    assert product_two.is_active is False
    assert system_collection.is_active is False
    assert shop_collection_row.is_active is False


def test_admin_reactivate_shop_reactivates_products_and_collections():
    session = next(get_session())
    now = datetime.now()
    unique = uuid.uuid4().hex[:8]

    owner = user(
        username=f"shopowner_reactivate_{unique}",
        email=f"shopowner_reactivate_{unique}@example.com",
        password_hash="hash",
        role=UserRole.SHOP_OWNER,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(owner)
    session.flush()

    target_shop = shop(
        owner_id=owner.id,
        display_id=uuid.uuid4().hex[:8],
        name=f"Reactivate Shop {unique}",
        year_established=2020,
        address="Main Street",
        city="Test City",
        phone_number="1234567890",
        email=f"shop_reactivate_{unique}@example.com",
        website_url=None,
        shop_logo_url="/images/logo.jpg",
        youtube_url=None,
        instagram_url=None,
        facebook_url=None,
        created_at=now,
        updated_at=now,
        is_active=False,
        approved=False,
    )
    session.add(target_shop)
    session.flush()

    product_one = product(
        display_id=uuid.uuid4().hex[:8],
        shop_id=target_shop.id,
        name=f"Reactivated Product {unique} 1",
        price=100,
        stock_quantity=5,
        created_at=now,
        updated_at=now,
        is_active=False,
    )
    session.add(product_one)
    session.flush()

    system_collection = collection(
        name=f"Reactivated Collection {unique}",
        description="Test collection",
        created_at=now,
        updated_at=now,
        is_active=False,
    )
    session.add(system_collection)
    session.flush()

    shop_collection_row = shop_collection(
        shop_id=target_shop.id,
        collection_id=system_collection.id,
        created_at=now,
        updated_at=now,
        is_active=False,
    )
    session.add(shop_collection_row)
    session.commit()

    reactivate_shop(target_shop.id, session)
    session.refresh(target_shop)
    session.refresh(product_one)
    session.refresh(system_collection)
    session.refresh(shop_collection_row)

    assert target_shop.is_active is True
    assert target_shop.approved is True
    assert product_one.is_active is True
    assert system_collection.is_active is True
    assert shop_collection_row.is_active is True


def test_list_collections_excludes_empty_collections():
    session = next(get_session())
    now = datetime.now()
    unique = uuid.uuid4().hex[:8]

    owner = user(
        username=f"collection_owner_{unique}",
        email=f"collection_owner_{unique}@example.com",
        password_hash="hash",
        role=UserRole.SHOP_OWNER,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(owner)
    session.flush()

    shop_row = shop(
        owner_id=owner.id,
        display_id=uuid.uuid4().hex[:8],
        name=f"Collection Shop {unique}",
        year_established=2020,
        address="Main Street",
        city="Test City",
        phone_number="1234567890",
        email=f"collection_shop_{unique}@example.com",
        website_url=None,
        shop_logo_url="/images/logo.jpg",
        youtube_url=None,
        instagram_url=None,
        facebook_url=None,
        created_at=now,
        updated_at=now,
        is_active=True,
        approved=True,
    )
    session.add(shop_row)
    session.flush()

    active_collection = collection(
        name=f"Populated Collection {unique}",
        description="Has products",
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    empty_collection = collection(
        name=f"Empty Collection {unique}",
        description="No products",
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add_all([active_collection, empty_collection])
    session.flush()

    product_row = product(
        display_id=uuid.uuid4().hex[:8],
        shop_id=shop_row.id,
        name=f"Collection Product {unique}",
        price=100,
        stock_quantity=4,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(product_row)
    session.flush()
    session.add(collection_product(
        collection_id=active_collection.id,
        product_id=product_row.id,
        created_at=now,
        updated_at=now,
        is_active=True,
    ))
    session.commit()

    response = list_collections(
        page=1,
        page_size=50,
        kind="system",
        shop_display_id=None,
        display_on_homepage=False,
        sort_by="newest",
        view_count=False,
        session=session,
        request=SimpleNamespace(state=SimpleNamespace(current_user=None)),
    )

    collection_ids = [item["id"] for item in response["items"]]
    assert active_collection.id in collection_ids
    assert empty_collection.id not in collection_ids
