from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import FloatField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class SessionForm(FlaskForm):
    title = StringField("Название", validators=[DataRequired(), Length(min=2, max=255)])
    ruleset_id = SelectField("Набор правил", coerce=int, validators=[DataRequired()])
    selected_strategy = SelectField(
        "Стратегия",
        choices=[
            ("generation", "Генерация-ориентированная"),
            ("consumer", "Потребительская"),
            ("balanced", "Сбалансированная"),
            ("storage", "Накопительная"),
            ("eco", "Экологическая"),
            ("risk_averse", "Риск-консервативная"),
            ("aggressive", "Агрессивная аукционная"),
        ],
        default="balanced",
        validators=[DataRequired()],
    )
    budget_total = FloatField(
        "Бюджет",
        default=200.0,
        validators=[DataRequired(), NumberRange(min=0.0)],
    )
    submit = SubmitField("Создать")


class ConfirmDeleteForm(FlaskForm):
    submit = SubmitField("Удалить сессию")


class SessionImportForm(FlaskForm):
    payload_json = TextAreaField("JSON сессии", validators=[Optional()])
    submit = SubmitField("Импортировать сессию")
