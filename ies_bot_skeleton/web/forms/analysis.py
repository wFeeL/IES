from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import SelectField, SubmitField
from wtforms.validators import Optional

from ..services.test_game_preset import TEST_GAME_BUNDLED_FORECAST_NAME


class ForecastSelectionForm(FlaskForm):
    selected_forecast_id = SelectField(
        "Активный прогноз",
        coerce=int,
        validators=[Optional()],
        choices=[(0, TEST_GAME_BUNDLED_FORECAST_NAME)],
        default=0,
    )
    submit = SubmitField("Выбрать прогноз")
