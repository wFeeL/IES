from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from flask_login import UserMixin
from sqlalchemy import Index
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="analyst")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Ruleset(db.Model):
    __tablename__ = "rulesets"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), nullable=False)
    version = db.Column(db.String(32), nullable=False, default="1")
    name = db.Column(db.String(128), nullable=False)
    config_json = db.Column(db.JSON, nullable=False, default=dict)
    is_builtin = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    sessions = db.relationship("GameSession", back_populates="ruleset", cascade="all")

    __table_args__ = (db.UniqueConstraint("code", "version", name="uq_ruleset_code_version"),)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "version": self.version,
            "name": self.name,
            "config_json": self.config_json,
            "is_builtin": self.is_builtin,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class GameSession(db.Model):
    __tablename__ = "game_sessions"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    ruleset_id = db.Column(db.Integer, db.ForeignKey("rulesets.id"), nullable=False)
    selected_strategy = db.Column(db.String(64), nullable=False, default="balanced")
    budget_total = db.Column(db.Float, nullable=False, default=9999.0)
    allpay_spent = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    ruleset = db.relationship("Ruleset", back_populates="sessions")
    objects = db.relationship(
        "ObjectInstance",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    lots = db.relationship("Lot", back_populates="session", cascade="all, delete-orphan")
    forecasts = db.relationship("Forecast", back_populates="session", cascade="all, delete-orphan")
    evaluations = db.relationship(
        "EvaluationResult",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "ruleset_id": self.ruleset_id,
            "selected_strategy": self.selected_strategy,
            "budget_total": self.budget_total,
            "allpay_spent": self.allpay_spent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ObjectType(db.Model):
    __tablename__ = "object_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    category = db.Column(db.String(32), nullable=False)
    subtype = db.Column(db.String(64), nullable=False, default="")
    description = db.Column(db.Text, nullable=False, default="")
    default_parameters_json = db.Column(db.JSON, nullable=False, default=dict)
    editable_fields_json = db.Column(db.JSON, nullable=False, default=list)
    rules_json = db.Column(db.JSON, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    instances = db.relationship("ObjectInstance", back_populates="object_type")
    lot_items = db.relationship("LotItem", back_populates="object_type")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "subtype": self.subtype,
            "description": self.description,
            "default_parameters": self.default_parameters_json,
            "editable_fields": self.editable_fields_json,
            "rules": self.rules_json,
            "is_active": self.is_active,
        }


class ObjectInstance(db.Model):
    __tablename__ = "object_instances"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("game_sessions.id"), nullable=False)
    object_type_id = db.Column(db.Integer, db.ForeignKey("object_types.id"), nullable=False)
    custom_name = db.Column(db.String(128), nullable=False, default="")
    current_parameters_json = db.Column(db.JSON, nullable=False, default=dict)
    source_lot_id = db.Column(db.Integer, db.ForeignKey("lots.id"), nullable=True)
    is_from_start_pack = db.Column(db.Boolean, nullable=False, default=False)
    parent_instance_id = db.Column(db.Integer, db.ForeignKey("object_instances.id"), nullable=True)
    district = db.Column(db.String(64), nullable=False, default="default")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    session = db.relationship("GameSession", back_populates="objects")
    object_type = db.relationship("ObjectType", back_populates="instances")
    source_lot = db.relationship("Lot", back_populates="generated_objects")
    parent = db.relationship(
        "ObjectInstance",
        remote_side=[id],
        backref=db.backref("children", lazy="dynamic"),
        uselist=False,
    )

    __table_args__ = (
        Index("ix_object_instance_session_object_type", "session_id", "object_type_id"),
    )

    def merged_parameters(self) -> Dict[str, Any]:
        base = dict(self.object_type.default_parameters_json or {})
        base.update(dict(self.current_parameters_json or {}))
        return base

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "object_type_id": self.object_type_id,
            "object_type_code": self.object_type.code if self.object_type else None,
            "custom_name": self.custom_name,
            "current_parameters": self.current_parameters_json,
            "source_lot_id": self.source_lot_id,
            "is_from_start_pack": self.is_from_start_pack,
            "parent_instance_id": self.parent_instance_id,
            "district": self.district,
            "is_active": self.is_active,
        }


class Lot(db.Model):
    __tablename__ = "lots"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("game_sessions.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    scope = db.Column(db.String(32), nullable=False, default="normal")
    status = db.Column(db.String(32), nullable=False, default="available")
    base_bid = db.Column(db.Float, nullable=False, default=0.0)
    current_bid = db.Column(db.Float, nullable=False, default=0.0)
    note = db.Column(db.Text, nullable=False, default="")
    available_round = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    session = db.relationship("GameSession", back_populates="lots")
    items = db.relationship("LotItem", back_populates="lot", cascade="all, delete-orphan")
    evaluations = db.relationship(
        "EvaluationResult", back_populates="lot", cascade="all, delete-orphan"
    )
    generated_objects = db.relationship("ObjectInstance", back_populates="source_lot")

    __table_args__ = (Index("ix_lot_session_status", "session_id", "status"),)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "name": self.name,
            "scope": self.scope,
            "status": self.status,
            "base_bid": self.base_bid,
            "current_bid": self.current_bid,
            "note": self.note,
            "available_round": self.available_round,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LotItem(db.Model):
    __tablename__ = "lot_items"

    id = db.Column(db.Integer, primary_key=True)
    lot_id = db.Column(db.Integer, db.ForeignKey("lots.id"), nullable=False)
    object_type_id = db.Column(db.Integer, db.ForeignKey("object_types.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    overrides_json = db.Column(db.JSON, nullable=False, default=dict)

    lot = db.relationship("Lot", back_populates="items")
    object_type = db.relationship("ObjectType", back_populates="lot_items")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "lot_id": self.lot_id,
            "object_type_id": self.object_type_id,
            "object_type_code": self.object_type.code if self.object_type else None,
            "quantity": self.quantity,
            "overrides": self.overrides_json,
        }


class Forecast(db.Model):
    __tablename__ = "forecasts"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("game_sessions.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    source_file = db.Column(db.String(255), nullable=False, default="")
    column_map_json = db.Column(db.JSON, nullable=False, default=dict)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    session = db.relationship("GameSession", back_populates="forecasts")
    periods = db.relationship(
        "ForecastPeriod",
        back_populates="forecast",
        cascade="all, delete-orphan",
        order_by="ForecastPeriod.tick",
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "name": self.name,
            "source_file": self.source_file,
            "column_map": self.column_map_json,
            "metadata": self.metadata_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "periods_count": len(self.periods),
        }


class ForecastPeriod(db.Model):
    __tablename__ = "forecast_periods"

    id = db.Column(db.Integer, primary_key=True)
    forecast_id = db.Column(db.Integer, db.ForeignKey("forecasts.id"), nullable=False)
    tick = db.Column(db.Integer, nullable=False)
    illumination = db.Column(db.Float, nullable=True)
    wind = db.Column(db.Float, nullable=True)
    market_price = db.Column(db.Float, nullable=True)
    consumption_json = db.Column(db.JSON, nullable=False, default=dict)
    extra_json = db.Column(db.JSON, nullable=False, default=dict)

    forecast = db.relationship("Forecast", back_populates="periods")

    __table_args__ = (
        db.UniqueConstraint("forecast_id", "tick", name="uq_forecast_period_tick"),
        Index("ix_forecast_period_forecast_tick", "forecast_id", "tick"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick": self.tick,
            "illumination": self.illumination,
            "wind": self.wind,
            "market_price": self.market_price,
            "consumption": self.consumption_json,
            "extra": self.extra_json,
        }


class EvaluationResult(db.Model):
    __tablename__ = "evaluation_results"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("game_sessions.id"), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey("lots.id"), nullable=False)
    mode = db.Column(db.String(32), nullable=False)
    scenario = db.Column(db.String(32), nullable=False, default="base")
    summary_score = db.Column(db.Float, nullable=False, default=0.0)
    metrics_json = db.Column(db.JSON, nullable=False, default=dict)
    explanation = db.Column(db.Text, nullable=False, default="")
    recommended_bid_soft = db.Column(db.Float, nullable=False, default=0.0)
    recommended_bid_hard = db.Column(db.Float, nullable=False, default=0.0)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    session = db.relationship("GameSession", back_populates="evaluations")
    lot = db.relationship("Lot", back_populates="evaluations")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "lot_id": self.lot_id,
            "mode": self.mode,
            "scenario": self.scenario,
            "summary_score": self.summary_score,
            "metrics": self.metrics_json,
            "explanation": self.explanation,
            "recommended_bid_soft": self.recommended_bid_soft,
            "recommended_bid_hard": self.recommended_bid_hard,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


Index(
    "ix_eval_session_lot_mode_created_desc",
    EvaluationResult.session_id,
    EvaluationResult.lot_id,
    EvaluationResult.mode,
    EvaluationResult.created_at.desc(),
)
