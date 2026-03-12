from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import SelectField, SubmitField
from wtforms.validators import Optional


class ForecastSelectionForm(FlaskForm):
    selected_forecast_id = SelectField(
        "Активный прогноз",
        coerce=int,
        validators=[Optional()],
        choices=[(0, "Встроенный базовый прогноз")],
        default=0,
    )
    submit = SubmitField("Выбрать прогноз")
