from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import FloatField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange


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
    budget_total = FloatField("Бюджет", validators=[DataRequired(), NumberRange(min=0.0)])
    submit = SubmitField("Создать")
