from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import IntegerField, StringField, SubmitField
from wtforms.validators import DataRequired, NumberRange


class ForecastUploadForm(FlaskForm):
    session_id = IntegerField("Session ID", validators=[DataRequired(), NumberRange(min=1)])
    name = StringField("Название", validators=[DataRequired()])
    file = FileField(
        "CSV",
        validators=[FileRequired(), FileAllowed(["csv"], "Только CSV")],
    )
    submit = SubmitField("Загрузить")
