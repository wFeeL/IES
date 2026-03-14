from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


class RulesetForm(FlaskForm):
    code = StringField("Код", validators=[DataRequired(), Length(min=2, max=64)])
    version = StringField("Версия", validators=[Optional(), Length(max=32)])
    name = StringField("Название", validators=[DataRequired(), Length(min=2, max=128)])
    active_start_pack_template_id = SelectField(
        "Активный стартовый пакет",
        coerce=int,
        validators=[Optional()],
        choices=[(0, "-- Без шаблона --")],
    )
    is_active = BooleanField("Активный")
    is_builtin = BooleanField("Системный")
    submit = SubmitField("Сохранить")


class RulesetCopyForm(FlaskForm):
    source_id = HiddenField("Source ID", validators=[DataRequired()])
    code = StringField("Код", validators=[Optional(), Length(max=64)])
    name = StringField("Название копии", validators=[DataRequired(), Length(min=2, max=128)])
    submit = SubmitField("Копировать")
