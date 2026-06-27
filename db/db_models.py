from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import (
    String,
    Integer,
    DateTime,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    UniqueConstraint,
)


class Base(DeclarativeBase):
    pass


class product_group(Base):
    __tablename__ = "product_groups"
    __table_args__ = (
        UniqueConstraint("id", "shop_id", name="uq_product_groups_id_shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(Integer, ForeignKey("shops.id"), nullable=False)
    display_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class product(Base):
    __tablename__ = "products"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_group_id", "shop_id"],
            ["product_groups.id", "product_groups.shop_id"],
            name="fk_products_group_shop",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(Integer, ForeignKey("shops.id"), nullable=False)
    product_group_id: Mapped[int] = mapped_column(Integer, ForeignKey("product_groups.id"), nullable=False)
    shop_product_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=True)
    price: Mapped[float] = mapped_column(nullable=False)
    discounted_price: Mapped[float] = mapped_column(nullable=True)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    year_established: Mapped[int] = mapped_column(Integer, nullable=True)
    address: Mapped[str] = mapped_column(String(500), nullable=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    website_url: Mapped[str] = mapped_column(String(255), nullable=True)
    shop_image_url: Mapped[str] = mapped_column(String(500), nullable=True)
    youtube_url: Mapped[str] = mapped_column(String(255), nullable=True)
    instagram_url: Mapped[str] = mapped_column(String(255), nullable=True)
    facebook_url: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

class collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(Integer, ForeignKey("shops.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

class collection_product(Base):
    __tablename__ = "collection_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("collections.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), nullable=False)
    display_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)

class shop_collection(Base):
    __tablename__ = "shop_collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(Integer, ForeignKey("shops.id"), nullable=False)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("collections.id"), nullable=False)
    display_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)

class shop_collection_product(Base):
    __tablename__ = "shop_collection_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_collections.id"), nullable=False)
    collection_product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), nullable=False)
    display_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)

class product_image(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    display_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)

class attribute_definition(Base):
    __tablename__ = "attribute_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attribute_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_filterable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)

class attribute_option(Base):
    __tablename__ = "attribute_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attribute_definition_id: Mapped[int] = mapped_column(Integer, ForeignKey("attribute_definitions.id"), nullable=False)
    option_value: Mapped[str] = mapped_column(String(255), nullable=False)
    display_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)

class product_attribute(Base):
    __tablename__ = "product_attributes"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "attribute_definition_id",
            name="uq_product_attribute_one_value_per_definition",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), nullable=False)
    attribute_definition_id: Mapped[int] = mapped_column(Integer, ForeignKey("attribute_definitions.id"), nullable=False)
    attribute_option_id: Mapped[int] = mapped_column(Integer, ForeignKey("attribute_options.id"), nullable=False)
    display_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)