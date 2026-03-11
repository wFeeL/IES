from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class ObjectTypeForm(FlaskForm):
    code = StringField("Код", validators=[DataRequired(), Length(min=2, max=64)])
    name = StringField("Название", validators=[DataRequired(), Length(min=2, max=128)])
    category = StringField("Категория", validators=[DataRequired(), Length(min=2, max=32)])
    subtype = StringField("Подтип", validators=[Optional(), Length(max=64)])
    description = TextAreaField("Описание", validators=[Optional()])
    default_parameters_json = TextAreaField("JSON параметров по умолчанию", validators=[Optional()])
    editable_fields_json = TextAreaField("JSON редактируемых полей", validators=[Optional()])
    rules_json = TextAreaField("JSON правил", validators=[Optional()])
    is_active = BooleanField("Активный", default=True)
    submit = SubmitField("Сохранить")
