import uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import (
    String,
    Integer,
    DateTime,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    UniqueConstraint, Index, text, Enum as SQLEnum
)

from enum import Enum

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"
    SHOP_OWNER = "shop_owner"


class Base(DeclarativeBase):
    pass


class product_group(Base):
    __tablename__ = "product_groups"
    __table_args__ = (
        UniqueConstraint("id", "shop_id", name="uq_product_groups_id_shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, default=lambda: str(uuid.uuid4().hex)[:8])
    shop_id: Mapped[int] = mapped_column(Integer, ForeignKey("shops.id", ondelete='CASCADE'), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class product(Base):
    __tablename__ = "products"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_group_id", "shop_id"],
            ["product_groups.id", "product_groups.shop_id"],
            name="fk_products_group_shop"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_id: Mapped[str] = mapped_column(String(8),unique=True,nullable=False, default=lambda: str(uuid.uuid4().hex)[:8])
    shop_id: Mapped[int] = mapped_column(Integer, ForeignKey("shops.id", ondelete='CASCADE'), nullable=False)
    product_group_id: Mapped[int] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=True)
    video_url: Mapped[str] = mapped_column(String(500), nullable=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_price: Mapped[int] = mapped_column(Integer, nullable=True)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, default=lambda: str(uuid.uuid4().hex)[:8])
    owner_id: Mapped[int] = mapped_column(Integer,ForeignKey("users.id", ondelete="RESTRICT"),unique=True,nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    year_established: Mapped[int] = mapped_column(Integer, nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    website_url: Mapped[str] = mapped_column(String(255), nullable=True)
    shop_logo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    youtube_url: Mapped[str] = mapped_column(String(255), nullable=True)
    instagram_url: Mapped[str] = mapped_column(String(255), nullable=True)
    facebook_url: Mapped[str] = mapped_column(String(255), nullable=True)
    # username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owner = relationship("user",back_populates="shop")

class user(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, default=lambda: str(uuid.uuid4().hex)[:8])
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    role: Mapped[UserRole] = mapped_column(SQLEnum(UserRole),default=UserRole.USER,nullable=False)
    shop = relationship("shop",back_populates="owner",uselist=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, default=lambda: str(uuid.uuid4().hex)[:8])
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class collection_product(Base):
    __tablename__ = "collection_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("collections.id", ondelete='CASCADE'), nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class collection_shop(Base):
    __tablename__ = "collection_shops"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("collections.id", ondelete='CASCADE'), nullable=False)
    shop_id: Mapped[int] = mapped_column(Integer, ForeignKey("shops.id", ondelete='CASCADE'), nullable=False)
    # optional store of shop display id for easier lookups
    shop_display_id: Mapped[str] = mapped_column(String(8), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)


class collection_attribute_option(Base):
    __tablename__ = "collection_attribute_options"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("collections.id", ondelete='CASCADE'), nullable=False)
    attribute_definition_id: Mapped[int] = mapped_column(Integer, ForeignKey("attribute_definitions.id", ondelete='CASCADE'), nullable=False)
    attribute_option_id: Mapped[int] = mapped_column(Integer, ForeignKey("attribute_options.id", ondelete='CASCADE'), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)

class shop_collection(Base):
    __tablename__ = "shop_collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, default=lambda: str(uuid.uuid4().hex)[:8])
    shop_id: Mapped[int] = mapped_column(Integer, ForeignKey("shops.id"), nullable=False)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("collections.id"), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class shop_collection_product(Base):
    __tablename__ = "shop_collection_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_collections.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class product_image(Base):
    __tablename__ = "product_images"

    __table_args__ = (
        Index("uq_primary_image_per_product", "product_id", unique=True, postgresql_where=text("primary_image = true")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer,ForeignKey("products.id", ondelete="CASCADE"),nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    primary_image: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class attribute_definition(Base):
    __tablename__ = "attribute_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, default=lambda: str(uuid.uuid4().hex)[:8])
    attribute_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_filterable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class attribute_option(Base):
    __tablename__ = "attribute_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attribute_definition_id: Mapped[int] = mapped_column(Integer, ForeignKey("attribute_definitions.id", ondelete="CASCADE"), nullable=False)
    option_value: Mapped[str] = mapped_column(String(255), nullable=False)
    display_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, default=lambda: str(uuid.uuid4().hex)[:8])
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

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
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    attribute_definition_id: Mapped[int] = mapped_column(Integer, ForeignKey("attribute_definitions.id", ondelete="CASCADE"), nullable=False)
    attribute_option_id: Mapped[int] = mapped_column(Integer, ForeignKey("attribute_options.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)