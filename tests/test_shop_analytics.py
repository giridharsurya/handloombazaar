from datetime import datetime

from api.analytics import AdminAnalyticsSummaryResponse, _get_period_config, _resolve_start_datetime


def test_week_period_config_is_7_days():
    config = _get_period_config("week")
    assert config["days"] == 7
    assert config["bucket_label"] == "day"
    assert config["item_count"] == 7


def test_month_period_config_is_30_days():
    config = _get_period_config("month")
    assert config["days"] == 30
    assert config["bucket_label"] == "day"
    assert config["item_count"] == 30


def test_all_period_defaults_to_entire_duration():
    config = _get_period_config("all")
    assert config["days"] is None
    assert config["bucket_label"] == "all"


def test_invalid_period_defaults_to_month():
    config = _get_period_config("random")
    assert config["days"] == 30
    assert config["bucket_label"] == "day"


def test_custom_date_range_uses_from_and_to_filters():
    start = _resolve_start_datetime("custom", from_date="2025-01-10", to_date="2025-01-20")
    assert start == datetime(2025, 1, 10)


def test_admin_analytics_summary_includes_top_system_collections():
    payload = {
        "site_visitor_count": 10,
        "system_collection_views": 45,
        "shop_views": 25,
        "total_shops": 8,
        "top_shops": [{"name": "Shop A", "value": "shop", "view_count": 12}],
        "top_system_collections": [{"name": "Festive Edit", "value": "system_collection", "view_count": 20}],
    }

    summary = AdminAnalyticsSummaryResponse(**payload)

    assert summary.top_system_collections[0].name == "Festive Edit"
    assert summary.top_system_collections[0].view_count == 20
