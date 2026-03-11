from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    FloatField,
    HiddenField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, NumberRange, Optional


class LotForm(FlaskForm):
    session_id = IntegerField("ID сессии", validators=[DataRequired(), NumberRange(min=1)])
    name = StringField("Название", validators=[DataRequired()])
    scope = SelectField(
        "Тип",
        choices=[
            ("start", "Стартовый"),
            ("normal", "Обычный"),
            ("local", "Локальный"),
            ("global", "Глобальный"),
        ],
        default="normal",
    )
    status = SelectField(
        "Статус",
        choices=[
            ("available", "Доступен"),
            ("bought", "Куплен"),
            ("rejected", "Отклонен"),
        ],
        default="available",
    )
    base_bid = FloatField("Стартовая цена", validators=[DataRequired(), NumberRange(min=0.0)])
    current_bid = FloatField("Текущая ставка", validators=[Optional(), NumberRange(min=0.0)])
    available_round = IntegerField("Раунд", validators=[Optional(), NumberRange(min=1)])
    note = TextAreaField("Заметки", validators=[Optional()])
    items_state_json = HiddenField("Состав (визуальный редактор)", validators=[Optional()])
    items_json = HiddenField("Состав JSON", validators=[Optional()])
    submit = SubmitField("Сохранить")


class ConfirmLotDeleteForm(FlaskForm):
    submit = SubmitField("Удалить лот")
