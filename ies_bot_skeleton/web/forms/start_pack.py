from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class StartPackTemplateForm(FlaskForm):
    code = StringField("Код", validators=[DataRequired(), Length(min=2, max=64)])
    name = StringField("Название", validators=[DataRequired(), Length(min=2, max=128)])
    description = TextAreaField("Описание", validators=[Optional()])
    is_active = BooleanField("Активный", default=True)
    is_builtin = BooleanField("Системный")
    items_state_json = HiddenField("Состав (визуальный)", validators=[Optional()])
    items_json = HiddenField("Состав JSON", validators=[Optional()])
    submit = SubmitField("Сохранить")
