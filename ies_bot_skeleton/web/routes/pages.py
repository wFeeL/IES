from __future__ import annotations

import json

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..forms import LoginForm, LotForm, SessionForm
from ..models import EvaluationResult, Forecast, GameSession, Lot, ObjectType, Ruleset, User
from ..services.auth import role_required
from ..services.evaluation import compare_lots, recommend_best_lot, strategy_fit
from ..services.network import validate_session_network

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    return redirect(url_for("pages.login"))


@pages_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.query(User).filter_by(username=form.username.data).one_or_none()
        if user is None or not user.check_password(form.password.data):
            flash("Неверный логин или пароль", "error")
        else:
            login_user(user, remember=bool(form.remember.data))
            return redirect(url_for("pages.dashboard"))

    return render_template("login.html", form=form)


@pages_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("pages.login"))


@pages_bp.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    form = SessionForm()
    rulesets = db.session.query(Ruleset).filter_by(is_active=True).all()
    if rulesets:
        form.ruleset_id.choices = [
            (row.id, f"{row.name} ({row.code}:{row.version})") for row in rulesets
        ]
        if not form.ruleset_id.data:
            form.ruleset_id.data = rulesets[0].id
    else:
        form.ruleset_id.choices = []

    if form.validate_on_submit():
        row = GameSession(
            title=form.title.data,
            ruleset_id=form.ruleset_id.data,
            selected_strategy=form.selected_strategy.data,
            budget_total=float(form.budget_total.data or 9999.0),
            allpay_spent=0.0,
        )
        db.session.add(row)
        db.session.commit()
        return redirect(url_for("pages.session_page", session_id=row.id))

    sessions = db.session.query(GameSession).order_by(GameSession.updated_at.desc()).all()
    return render_template("dashboard.html", sessions=sessions, form=form)


@pages_bp.get("/sessions/<int:session_id>")
@login_required
def session_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    return render_template("session.html", session=session)


@pages_bp.get("/catalog")
@login_required
def catalog():
    rows = db.session.query(ObjectType).order_by(ObjectType.code).all()
    return render_template("catalog.html", object_types=rows)


@pages_bp.get("/system/<int:session_id>")
@login_required
def system_view(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    issues = validate_session_network(list(session.objects))
    return render_template("system.html", session=session, issues=issues)


@pages_bp.get("/lots/<int:session_id>")
@login_required
def lots_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).order_by(Lot.id).all()
    return render_template("lots.html", session=session, lots=lots)


@pages_bp.route("/lots/<int:session_id>/edit", methods=["GET", "POST"])
@login_required
def lots_edit(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    form = LotForm()
    form.session_id.data = session_id

    if form.validate_on_submit():
        lot = Lot(
            session_id=session_id,
            name=form.name.data,
            scope=form.scope.data,
            status=form.status.data,
            base_bid=float(form.base_bid.data or 0.0),
            current_bid=float(form.current_bid.data or 0.0),
            note=form.note.data or "",
            available_round=int(form.available_round.data or 1),
        )
        db.session.add(lot)
        db.session.flush()

        raw_items = form.items_json.data or "[]"
        try:
            items_payload = json.loads(raw_items)
            if not isinstance(items_payload, list):
                raise ValueError("items_json должен быть массивом")
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            flash(f"Некорректный JSON состава лота: {exc}", "error")
            return render_template("lot_edit.html", session=session, form=form)

        from .api import (
            _lot_items_from_payload,
        )  # local import to avoid circular import at module import time

        _lot_items_from_payload(lot, items_payload)
        if not lot.items:
            db.session.rollback()
            flash("Лот не может быть пустым", "error")
            return render_template("lot_edit.html", session=session, form=form)

        db.session.add(lot)
        db.session.commit()
        flash("Лот сохранен", "success")
        return redirect(url_for("pages.lots_page", session_id=session_id))

    return render_template("lot_edit.html", session=session, form=form)


@pages_bp.get("/forecast/<int:session_id>")
@login_required
def forecast_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    forecasts = (
        db.session.query(Forecast)
        .filter_by(session_id=session_id)
        .order_by(Forecast.id.desc())
        .all()
    )
    return render_template("forecast.html", session=session, forecasts=forecasts)


@pages_bp.get("/evaluation/<int:session_id>")
@login_required
def evaluation_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    evaluations = (
        db.session.query(EvaluationResult)
        .filter_by(session_id=session_id)
        .order_by(EvaluationResult.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template("evaluation.html", session=session, evaluations=evaluations)


@pages_bp.get("/compare/<int:session_id>")
@login_required
def compare_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    ranking = compare_lots(session=session, lots=lots, mode="forecast") if lots else []
    return render_template("compare.html", session=session, lots=lots, ranking=ranking)


@pages_bp.get("/recommend/<int:session_id>")
@login_required
def recommend_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    recommendation = (
        recommend_best_lot(session=session, lots=lots, mode="forecast") if lots else None
    )
    return render_template(
        "recommend.html", session=session, lots=lots, recommendation=recommendation
    )


@pages_bp.get("/quick-auction/<int:session_id>")
@login_required
def quick_auction_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    ranking = compare_lots(session=session, lots=lots, mode="forecast") if lots else []
    return render_template("quick_auction.html", session=session, lots=lots, ranking=ranking)


@pages_bp.get("/settings/model")
@login_required
@role_required("admin")
def settings_model_page():
    rulesets = db.session.query(Ruleset).order_by(Ruleset.created_at.desc()).all()
    return render_template("settings_model.html", rulesets=rulesets)


@pages_bp.get("/strategy-fit/<int:lot_id>")
@login_required
def strategy_fit_page(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return redirect(url_for("pages.dashboard"))
    session = db.session.get(GameSession, lot.session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    fit = strategy_fit(session=session, lot=lot, mode="forecast")
    return render_template("strategy_fit.html", session=session, lot=lot, fit=fit)
