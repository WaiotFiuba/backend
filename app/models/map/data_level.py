from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DOUBLE_PRECISION,
    ForeignKey,
    Integer,
    String,
    TIMESTAMP,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.map_database import MapBase


class DataLevel(MapBase):
    __tablename__ = "data_level"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    imei: Mapped[str | None] = mapped_column(String)
    m_id: Mapped[str | None] = mapped_column(String)
    reading_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reported_height: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    echo: Mapped[str | None] = mapped_column(Text)
    fire_alarm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    freeze_alarm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reported_temperature: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    crash_alarm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    garbage_collection_alarm: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    reported_collection_date: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    reported_low_consumption_voltage: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    reported_high_consumption_voltage: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    container_current_level_old: Mapped[int | None] = mapped_column(Integer)
    site_pickup_status: Mapped[str | None] = mapped_column(String)
    client_id: Mapped[int | None] = mapped_column(BigInteger)
    device_id: Mapped[int | None] = mapped_column(BigInteger)
    device_name: Mapped[str | None] = mapped_column(String)
    container_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("containers.id"),
    )
    container_name: Mapped[str | None] = mapped_column(String)
    container_current_level: Mapped[int | None] = mapped_column(Integer)
    container_type_id: Mapped[int | None] = mapped_column(BigInteger)
    container_type_name: Mapped[str | None] = mapped_column(String)
    container_type_height: Mapped[int | None] = mapped_column(Integer)
    site_id: Mapped[str | None] = mapped_column(String)
    site_name: Mapped[str | None] = mapped_column(String)
    zone_id: Mapped[int | None] = mapped_column(BigInteger)
    zone_name: Mapped[str | None] = mapped_column(String)
    waste_type_id: Mapped[int | None] = mapped_column(BigInteger)
    waste_type_name: Mapped[str | None] = mapped_column(String)
    waste_type_pickup_alert_threshold: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION
    )
    waste_type_pickup_warning_threshold: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION
    )
    waste_type_max_days_between_pickup: Mapped[int | None] = mapped_column(Integer)
    waste_type_alert_rule_category_id: Mapped[int | None] = mapped_column(BigInteger)
    waste_type_alert_rule_category_name: Mapped[str | None] = mapped_column(String)
    rssi: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    container_current_m3: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    container_current_t: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    provider_id: Mapped[int | None] = mapped_column(BigInteger)
    message_version: Mapped[str | None] = mapped_column(String)
    device_serial_id: Mapped[str | None] = mapped_column(String)
    device_model_id: Mapped[int | None] = mapped_column(BigInteger)
    device_model_name: Mapped[str | None] = mapped_column(String)
    device_category: Mapped[str | None] = mapped_column(String)
    container_serie_id: Mapped[str | None] = mapped_column(String)
    load_side_category: Mapped[str | None] = mapped_column(String)
