from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import HiddenField, IntegerField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, NumberRange, Optional


class ObjectInstanceForm(FlaskForm):
    session_id = IntegerField("Session ID", validators=[DataRequired(), NumberRange(min=1)])
    object_type_id = IntegerField("ObjectType ID", validators=[DataRequired(), NumberRange(min=1)])
    custom_name = StringField("Имя", validators=[Optional()])
    district = StringField("Энергорайон", validators=[Optional()])
    parent_instance_id = IntegerField("Родитель", validators=[Optional(), NumberRange(min=1)])
    current_parameters_json = TextAreaField("Параметры JSON", validators=[Optional()])
    source_lot_id = IntegerField("Лот-источник", validators=[Optional(), NumberRange(min=1)])
    is_from_start_pack = HiddenField("is_from_start_pack", default="0")
    submit = SubmitField("Сохранить")
