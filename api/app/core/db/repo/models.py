# app/core/db/repo/qc/models.py
from __future__ import annotations

from typing import Optional, List
from sqlalchemy import (
    String, Boolean, ForeignKey, UniqueConstraint, Numeric, Text,
    func, Integer, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, ENUM as PGEnum
from sqlalchemy.types import DateTime

from app.core.db.session import Base

# --- Postgres ENUMs (already created by migrations) ---
StationEnum      = PGEnum("ROLL", "BUNDLE", name="station",  create_type=False)
ReviewTypeEnum   = PGEnum("DEFECT_FIX", "SCRAP_FROM_RECHECK", name="review_type",  create_type=False)
ReviewStateEnum  = PGEnum("PENDING", "APPROVED", "REJECTED", name="review_state",  create_type=False)
ImageKindEnum    = PGEnum("DETECTED", "FIX", "OTHER", name="image_kind",  create_type=False)

# =========================
# Master tables
# =========================

class ProductionLine(Base):
    __tablename__ = "production_lines"
    __table_args__ = {"schema": "qc"}

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items: Mapped[List["Item"]] = relationship(back_populates="line")


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = {"schema": "qc"}

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_time: Mapped[str] = mapped_column(String, nullable=False)  # TIME → store as string or Time if you prefer
    end_time: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DefectType(Base):
    __tablename__ = "defect_types"
    __table_args__ = {"schema": "qc"}

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    code: Mapped[Optional[str]] = mapped_column(String, unique=True)
    name_th: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    item_defects: Mapped[List["ItemDefect"]] = relationship(back_populates="defect_type")


class ItemStatus(Base):
    __tablename__ = "item_statuses"
    __table_args__ = {"schema": "qc"}

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name_th: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    items: Mapped[List["Item"]] = relationship(back_populates="status")

# =========================
# Items
# =========================

class Item(Base):
    __tablename__ = "items"
    __table_args__ = {"schema": "qc"}

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)

    station: Mapped[str] = mapped_column(StationEnum, nullable=False)  # qc.station enum
    line_id: Mapped[int] = mapped_column(ForeignKey("qc.production_lines.id", onupdate="CASCADE"), nullable=False)

    product_code: Mapped[Optional[str]] = mapped_column(String)
    roll_number: Mapped[Optional[str]] = mapped_column(String)
    bundle_number: Mapped[Optional[str]] = mapped_column(String)
    job_order_number: Mapped[Optional[str]] = mapped_column(String)
    roll_width: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))

    detected_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    item_status_id: Mapped[int] = mapped_column(ForeignKey("qc.item_statuses.id"), nullable=False)
    ai_note: Mapped[Optional[str]] = mapped_column(Text)

    scrap_requires_qc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scrap_confirmed_by: Mapped[Optional[int]] = mapped_column(ForeignKey('user.users.id'))
    scrap_confirmed_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True))

    # NOTE: migration didn't declare FK; keep as plain column to match DB
    current_review_id: Mapped[Optional[int]] = mapped_column(BIGINT)

    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True))

    # relationships
    line: Mapped["ProductionLine"] = relationship(back_populates="items")
    status: Mapped["ItemStatus"] = relationship(back_populates="items")

    defects: Mapped[List["ItemDefect"]] = relationship(back_populates="item", cascade="all, delete-orphan")
    reviews: Mapped[List["Review"]] = relationship(back_populates="item", cascade="all, delete-orphan")
    images: Mapped[List["ItemImage"]] = relationship(back_populates="item", cascade="all, delete-orphan")
    events: Mapped[List["ItemEvent"]] = relationship(back_populates="item", cascade="all, delete-orphan")

    # bundle mapping (if this is a bundle → mapping to rolls; or if roll → backrefs)
    bundle_rolls: Mapped[List["BundleRoll"]] = relationship(
        back_populates="bundle_item",
        foreign_keys="BundleRoll.bundle_item_id",
        cascade="all, delete-orphan",
    )
    roll_of_bundles: Mapped[List["BundleRoll"]] = relationship(
        back_populates="roll_item",
        foreign_keys="BundleRoll.roll_item_id",
        cascade="all, delete-orphan",
    )

# Helpful ORM-side indexes (optional; DB has them already in migration)
Index("ix_qc_items_status_time", Item.item_status_id, Item.detected_at.desc())
Index("ix_qc_items_line_time", Item.line_id, Item.detected_at.desc())

# =========================
# Item ⇄ Defects (M:N)
# =========================

class ItemDefect(Base):
    __tablename__ = "item_defects"
    __table_args__ = (
        UniqueConstraint("item_id", "defect_type_id", name="uq_item_defects_item_type"),
        {"schema": "qc"},
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("qc.items.id", ondelete="CASCADE"), nullable=False)
    defect_type_id: Mapped[int] = mapped_column(ForeignKey("qc.defect_types.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    meta: Mapped[Optional[dict]] = mapped_column(JSONB)

    item: Mapped["Item"] = relationship(back_populates="defects")
    defect_type: Mapped["DefectType"] = relationship(back_populates="item_defects")

# =========================
# Bundle <-> Rolls mapping
# =========================

class BundleRoll(Base):
    __tablename__ = "bundle_rolls"
    __table_args__ = (
        UniqueConstraint("bundle_item_id", "roll_item_id", name="uq_bundle_roll_pair"),
        {"schema": "qc"},
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    bundle_item_id: Mapped[int] = mapped_column(ForeignKey("qc.items.id", ondelete="CASCADE"), nullable=False)
    roll_item_id: Mapped[int] = mapped_column(ForeignKey("qc.items.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bundle_item: Mapped["Item"] = relationship(
        "Item", foreign_keys=[bundle_item_id], back_populates="bundle_rolls"
    )
    roll_item: Mapped["Item"] = relationship(
        "Item", foreign_keys=[roll_item_id], back_populates="roll_of_bundles"
    )

# =========================
# Reviews
# =========================

class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = {"schema": "qc"}

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("qc.items.id", ondelete="CASCADE"), nullable=False)

    review_type: Mapped[str] = mapped_column(ReviewTypeEnum, nullable=False)
    state: Mapped[str] = mapped_column(ReviewStateEnum, nullable=False, default="PENDING")

    submitted_by: Mapped[int] = mapped_column(ForeignKey('user.users.id'), nullable=False)
    submitted_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submit_note: Mapped[Optional[str]] = mapped_column(Text)

    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey('user.users.id'))
    reviewed_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[Optional[str]] = mapped_column(Text)

    defect_type_id: Mapped[Optional[int]] = mapped_column(ForeignKey("qc.defect_types.id"))
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    item: Mapped["Item"] = relationship(back_populates="reviews")
    defect_type: Mapped[Optional["DefectType"]] = relationship()

# =========================
# Item images
# =========================

class ItemImage(Base):
    __tablename__ = "item_images"
    __table_args__ = {"schema": "qc"}

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("qc.items.id", ondelete="CASCADE"), nullable=False)
    review_id: Mapped[Optional[int]] = mapped_column(ForeignKey("qc.reviews.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(ImageKindEnum, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[Optional[int]] = mapped_column(ForeignKey('user.users.id'))
    uploaded_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    meta: Mapped[Optional[dict]] = mapped_column(JSONB)

    item: Mapped["Item"] = relationship(back_populates="images")

# =========================
# Item events (audit log)
# =========================

class ItemEvent(Base):
    __tablename__ = "item_events"
    __table_args__ = {"schema": "qc"}

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("qc.items.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey('user.users.id'))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_status_id: Mapped[Optional[int]] = mapped_column(ForeignKey("qc.item_statuses.id"))
    to_status_id: Mapped[Optional[int]] = mapped_column(ForeignKey("qc.item_statuses.id"))
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    item: Mapped["Item"] = relationship(back_populates="events")
