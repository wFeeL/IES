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
    base_bid = FloatField("Стартовая цена", validators=[DataRequired(), NumberRange(min=0.0)])
    current_bid = FloatField("Текущая ставка", validators=[Optional(), NumberRange(min=0.0)])
    note = TextAreaField("Заметки", validators=[Optional()])
    items_state_json = HiddenField("Состав (визуальный редактор)", validators=[Optional()])
    items_json = HiddenField("Состав JSON", validators=[Optional()])
    submit = SubmitField("Сохранить")


class ConfirmLotDeleteForm(FlaskForm):
    submit = SubmitField("Удалить лот")


class LotPurchaseForm(FlaskForm):
    purchase_price = FloatField(
        "Фактическая цена покупки",
        validators=[DataRequired(), NumberRange(min=0.01)],
    )
    submit = SubmitField("Купить лот")


class LotUndoPurchaseForm(FlaskForm):
    submit = SubmitField("Отменить покупку")


class LotRejectForm(FlaskForm):
    submit = SubmitField("Отклонить лот")


class LotRestoreForm(FlaskForm):
    submit = SubmitField("Вернуть в доступные")
