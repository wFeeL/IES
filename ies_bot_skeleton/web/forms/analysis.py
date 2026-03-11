from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import FloatField, SelectField, SubmitField
from wtforms.validators import DataRequired, NumberRange, Optional


class AnalysisModeForm(FlaskForm):
    analysis_mode = SelectField(
        "Режим анализа",
        choices=[
            ("no_forecast", "Без прогноза"),
            ("forecast", "С прогнозом"),
        ],
        validators=[DataRequired()],
        default="no_forecast",
    )
    submit = SubmitField("Сохранить режим")


class CorridorSettingsForm(FlaskForm):
    consumer_load_pct = FloatField(
        "Коридор нагрузки, %",
        validators=[DataRequired(), NumberRange(min=0.0, max=100.0)],
        default=10.0,
    )
    producer_generation_pct = FloatField(
        "Коридор генерации, %",
        validators=[DataRequired(), NumberRange(min=0.0, max=100.0)],
        default=10.0,
    )
    solar_output_pct = FloatField(
        "Override для солнца, %",
        validators=[Optional(), NumberRange(min=0.0, max=100.0)],
    )
    wind_output_pct = FloatField(
        "Override для ветра, %",
        validators=[Optional(), NumberRange(min=0.0, max=100.0)],
    )
    submit = SubmitField("Сохранить коридор")


class ForecastSelectionForm(FlaskForm):
    selected_forecast_id = SelectField(
        "Активный прогноз",
        coerce=int,
        validators=[Optional()],
        choices=[(0, "Не выбран")],
        default=0,
    )
    submit = SubmitField("Выбрать прогноз")
