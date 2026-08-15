import os
import re
from datetime import datetime
from typing import BinaryIO
from urllib.parse import unquote, urlparse

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient


def _normalize_container_part(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = text.replace("_", " ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def build_shop_container_name(
    shop_display_id: str,
    shop_name: str | None = None,
    city: str | None = None,
    created_at: datetime | None = None,
) -> str:
    return "shops"


def build_shop_blob_prefix(
    shop_display_id: str,
    shop_name: str | None = None,
    city: str | None = None,
    created_at: datetime | None = None,
) -> str:
    display_id = _normalize_container_part(shop_display_id)
    name = _normalize_container_part(shop_name)
    city_name = _normalize_container_part(city)
    time_part = ""
    if created_at is not None:
        time_part = created_at.strftime("%Y%m%d%H%M%S")

    parts = [part for part in [display_id, name, city_name, time_part] if part]
    return "--".join(parts) if parts else "shop-images"


def build_product_blob_prefix(
    shop_display_id: str,
    product_display_id: str,
    shop_name: str | None = None,
    city: str | None = None,
    created_at: datetime | None = None,
    product_name: str | None = None,
    product_created_at: datetime | None = None,
) -> str:
    shop_prefix = build_shop_blob_prefix(shop_display_id, shop_name=shop_name, city=city, created_at=created_at)
    product_id = _normalize_container_part(product_display_id)
    product_label = _normalize_container_part(product_name)
    product_time = ""
    if product_created_at is not None:
        product_time = product_created_at.strftime("%Y%m%d%H%M%S")

    product_parts = [part for part in [product_id, product_label, product_time] if part]
    product_suffix = "--".join(product_parts) if product_parts else "product"
    return f"{shop_prefix}/{product_suffix}"


def _get_blob_service_client() -> BlobServiceClient:
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        return BlobServiceClient.from_connection_string(connection_string)

    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    if not account_name:
        raise ValueError("AZURE_STORAGE_ACCOUNT_NAME is required when not using connection string")

    account_url = f"https://{account_name}.blob.core.windows.net"
    credential = DefaultAzureCredential()
    return BlobServiceClient(account_url=account_url, credential=credential)


def ensure_shop_container_exists(container_name: str, shop_display_id: str | None = None) -> str:
    client = _get_blob_service_client()
    container_client = client.get_container_client(container_name)
    if not container_client.exists():
        container_client.create_container()
    return container_name


def upload_image_to_shop_container(
    file: BinaryIO,
    shop_display_id: str,
    original_filename: str,
    shop_name: str | None = None,
    city: str | None = None,
    created_at: datetime | None = None,
) -> str:
    if not shop_display_id:
        raise ValueError("shop_display_id is required")

    container_name = build_shop_container_name(shop_display_id, shop_name=shop_name, city=city, created_at=created_at)
    ensure_shop_container_exists(container_name, shop_display_id)

    file.seek(0)
    data = file.read()
    if not data:
        raise ValueError("Uploaded file is empty")

    safe_name = os.path.basename(original_filename or "upload")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name)
    if not safe_name:
        safe_name = "upload"

    prefix = build_shop_blob_prefix(shop_display_id, shop_name=shop_name, city=city, created_at=created_at)
    blob_name = f"{prefix}/{os.urandom(8).hex()}_{safe_name}"

    client = _get_blob_service_client()
    blob_client = client.get_blob_client(container=container_name, blob=blob_name)
    blob_client.upload_blob(data, overwrite=True)
    return blob_client.url


def upload_product_image_to_blob(
    file: BinaryIO,
    shop_display_id: str,
    product_display_id: str,
    original_filename: str,
    shop_name: str | None = None,
    city: str | None = None,
    shop_created_at: datetime | None = None,
    product_name: str | None = None,
    product_created_at: datetime | None = None,
) -> str:
    if not shop_display_id:
        raise ValueError("shop_display_id is required")
    if not product_display_id:
        raise ValueError("product_display_id is required")

    container_name = build_shop_container_name(shop_display_id, shop_name=shop_name, city=city, created_at=shop_created_at)
    ensure_shop_container_exists(container_name)

    file.seek(0)
    data = file.read()
    if not data:
        raise ValueError("Uploaded file is empty")

    safe_name = os.path.basename(original_filename or "upload")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name)
    if not safe_name:
        safe_name = "upload"

    prefix = build_product_blob_prefix(
        shop_display_id,
        product_display_id,
        shop_name=shop_name,
        city=city,
        created_at=shop_created_at,
        product_name=product_name,
        product_created_at=product_created_at,
    )
    blob_name = f"{prefix}/{os.urandom(8).hex()}_{safe_name}"

    client = _get_blob_service_client()
    blob_client = client.get_blob_client(container=container_name, blob=blob_name)
    blob_client.upload_blob(data, overwrite=True)
    return blob_client.url


def delete_blob_by_url(blob_url: str | None) -> bool:
    if not blob_url:
        return False

    parsed = urlparse(blob_url)
    if parsed.scheme not in {"http", "https"}:
        return False

    path = unquote(parsed.path).lstrip("/")
    if not path or "/" not in path:
        return False

    container_name, blob_name = path.split("/", 1)
    if not container_name or not blob_name:
        return False

    try:
        client = _get_blob_service_client()
        blob_client = client.get_blob_client(container=container_name, blob=blob_name)
        blob_client.delete_blob()
        return True
    except Exception:
        return False
