from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="started")
    balance: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    upsell_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    upsell_shown: Mapped[bool] = mapped_column(Boolean, default=False)
    upsell_offer_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_video_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    funnel_uploaded_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    funnel_selected_trend: Mapped[bool] = mapped_column(Boolean, default=False)
    funnel_paid: Mapped[bool] = mapped_column(Boolean, default=False)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending_payment")
    package_type: Mapped[int] = mapped_column(Integer)
    photo_file_ids_json: Mapped[str] = mapped_column(Text)
    trend_indices_json: Mapped[str] = mapped_column(Text)
    trend_urls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    yookassa_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    generations: Mapped[list["Generation"]] = relationship(back_populates="order")


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    photo_file_id: Mapped[str] = mapped_column(String(512))
    trend_index: Mapped[int] = mapped_column(Integer)
    reference_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    kie_task_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    result_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    notify_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    order: Mapped["Order | None"] = relationship(back_populates="generations")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount_rub: Mapped[int] = mapped_column(Integer)
    yookassa_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
