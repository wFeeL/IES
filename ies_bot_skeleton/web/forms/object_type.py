from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from ..services.admin_schemas import OBJECT_TYPE_CATEGORY_CHOICES, OBJECT_TYPE_SUBTYPE_CHOICES


class ObjectTypeForm(FlaskForm):
    code = StringField("Код", validators=[DataRequired(), Length(min=2, max=64)])
    name = StringField("Название", validators=[DataRequired(), Length(min=2, max=128)])
    category = SelectField("Категория", validators=[DataRequired()], choices=OBJECT_TYPE_CATEGORY_CHOICES)
    subtype = SelectField("Подтип", validators=[Optional()], choices=OBJECT_TYPE_SUBTYPE_CHOICES)
    description = TextAreaField("Описание", validators=[Optional()])
    is_active = BooleanField("Активный", default=True)
    submit = SubmitField("Сохранить")
