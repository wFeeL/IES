from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Optional


class ObjectInstanceForm(FlaskForm):
    session_id = HiddenField("ID сессии", validators=[DataRequired()])
    object_type_id = SelectField("Тип объекта", coerce=int, validators=[DataRequired()], choices=[])
    custom_name = StringField("Имя", validators=[Optional()])
    district = StringField("Энергорайон", validators=[Optional()])
    parent_instance_id = SelectField("Родитель", coerce=int, validators=[Optional()], choices=[(0, "Без родителя")])
    is_active = BooleanField("Активен", default=True)
    submit = SubmitField("Сохранить")
