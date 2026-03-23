from __future__ import annotations

import pytest

from ies_bot_skeleton.domain.ies2026 import (
    EnergyObject,
    anti_dumping_cap_mw,
    evaluate_candidate_bundle,
    simulate_system,
    validate_network,
)
from ies_bot_skeleton.web.services.ruleset import build_default_ruleset_config


def _forecast_pack(
    *,
    buy_price: float = 8.4,
    sell_price: float = 7.8,
    illumination_peak: float = 0.9,
    wind_base: float = 9.0,
) -> dict:
    ticks = range(48)
    solar = {}
    wind_main = {}
    wind_west = {}
    house_a = {}
    office = {}
    factory = {}
    hospital = {}
    buy = {}
    sell = {}
    balancing = {}
    bid_cap = {}
    for tick in ticks:
        daylight = illumination_peak if 12 <= tick <= 32 else 0.12
        solar[tick] = daylight
        wind_main[tick] = wind_base + (tick % 5) * 0.7
        wind_west[tick] = wind_base - 1.2 + (tick % 7) * 0.5
        house_a[tick] = 4.0 + (2.2 if tick < 8 or tick > 38 else 0.4)
        office[tick] = 2.0 + (3.8 if 10 <= tick <= 30 else 0.3)
        factory[tick] = 7.0 + (1.0 if tick % 6 < 3 else 0.2)
        hospital[tick] = 5.4
        buy[tick] = buy_price + (1.6 if tick < 7 or tick > 40 else 0.0)
        sell[tick] = sell_price + (1.4 if 13 <= tick <= 28 else 0.0)
        balancing[tick] = 6.2
        bid_cap[tick] = 120.0
    return {
        "solar": {"solar": solar},
        "wind": {"wind_main": wind_main, "wind_west": wind_west},
        "load": {
            "house_a": house_a,
            "office": office,
            "factory": factory,
            "hospital": hospital,
        },
        "market": {
            "price": buy,
            "sell_price": sell,
            "balancing_penalty_price": balancing,
            "bid_cap_mw": bid_cap,
        },
    }


def _main(*, ports: int = 8) -> EnergyObject:
    return EnergyObject(
        object_id="main",
        object_type_id=1,
        code="main_substation",
        name="Главная подстанция",
        category="infrastructure",
        district="trunk",
        parameters={"ports": ports},
    )


def _mini(object_id: str, *, district: str, parent_id: str = "main", ports: int = 5) -> EnergyObject:
    return EnergyObject(
        object_id=object_id,
        object_type_id=2,
        code="mini_substation",
        name=object_id,
        category="infrastructure",
        district=district,
        parameters={"ports": ports},
        parent_id=parent_id,
    )


def _consumer(
    object_id: str,
    code: str,
    *,
    district: str,
    parent_id: str,
    tariff: float,
    demand: float,
    connection_point: str = "A",
) -> EnergyObject:
    return EnergyObject(
        object_id=object_id,
        object_type_id=10,
        code=code,
        name=object_id,
        category="consumer",
        district=district,
        parameters={
            "tariff_rub_per_tick": tariff,
            "tariff_rub_per_mw_tick": tariff,
            "expected_consumption_mw": demand,
            "elasticity": 0.1,
            "connection_point": connection_point,
        },
        parent_id=parent_id,
        is_candidate=True,
    )


def _solar(
    object_id: str,
    *,
    district: str,
    parent_id: str,
    contract: float,
    generation: float = 20.0,
    connection_point: str = "A",
) -> EnergyObject:
    return EnergyObject(
        object_id=object_id,
        object_type_id=20,
        code="solar",
        name=object_id,
        category="generator",
        district=district,
        parameters={
            "contract_rub_per_tick": contract,
            "generation_mw": generation,
            "efficiency": 0.95,
            "connection_point": connection_point,
        },
        parent_id=parent_id,
        is_candidate=True,
    )


def _wind(
    object_id: str,
    *,
    district: str,
    parent_id: str,
    contract: float,
    channel: str = "wind_main",
) -> EnergyObject:
    return EnergyObject(
        object_id=object_id,
        object_type_id=21,
        code="wind",
        name=object_id,
        category="generator",
        district=district,
        parameters={
            "contract_rub_per_tick": contract,
            "generation_mw": 18.0,
            "wind_channel": channel,
            "rated_mps": 11.0,
            "cut_in_mps": 3.0,
            "cut_out_mps": 25.0,
            "connection_point": "A",
        },
        parent_id=parent_id,
        is_candidate=True,
    )


def _storage(
    object_id: str,
    *,
    district: str,
    parent_id: str,
    contract: float,
) -> EnergyObject:
    return EnergyObject(
        object_id=object_id,
        object_type_id=30,
        code="storage",
        name=object_id,
        category="storage",
        district=district,
        parameters={
            "contract_rub_per_tick": contract,
            "capacity_mw_tick": 120.0,
            "charge_rate_mw_tick": 15.0,
            "discharge_rate_mw_tick": 20.0,
            "roundtrip_efficiency": 0.93,
            "connection_point": "A",
        },
        parent_id=parent_id,
        is_candidate=True,
    )


def test_consumer_lot_has_positive_delta_profit_and_break_even_tariff():
    evaluation = evaluate_candidate_bundle(
        lot_id=1,
        lot_name="Дом типа A",
        base_objects=[_main(), _mini("load-mini", district="load_north")],
        candidate_objects=[
            _consumer(
                "house-a",
                "house_a",
                district="load_north",
                parent_id="load-mini",
                tariff=45.0,
                demand=4.0,
            )
        ],
        forecast_pack=_forecast_pack(),
        ruleset_config=build_default_ruleset_config(),
    )

    assert evaluation.auction_direction == "descending_consumer_tariff"
    assert evaluation.expected_delta_profit > 0.0
    assert evaluation.break_even_tariff > 0.0
    assert evaluation.recommended_bid_or_tariff >= evaluation.break_even_tariff
    assert evaluation.topology.blocking is False
    assert evaluation.minimum_acceptable_tariff == evaluation.break_even_tariff
    assert evaluation.recommended_walkdown_tariff >= evaluation.minimum_acceptable_tariff


def test_generator_lot_uses_weather_and_market_based_value():
    evaluation = evaluate_candidate_bundle(
        lot_id=2,
        lot_name="СЭС",
        base_objects=[_main(), _mini("gen-mini", district="gen_west")],
        candidate_objects=[
            _solar(
                "solar-1",
                district="gen_west",
                parent_id="gen-mini",
                contract=3.5,
                generation=20.0,
            )
        ],
        forecast_pack=_forecast_pack(sell_price=8.4),
        ruleset_config=build_default_ruleset_config(),
    )

    assert evaluation.auction_direction == "ascending_service_tariff"
    assert evaluation.expected_delta_profit > 0.0
    assert evaluation.recommended_bid_or_tariff < evaluation.break_even_tariff
    assert evaluation.market_risk in {"low", "medium", "high"}
    assert evaluation.maximum_acceptable_service_tariff == evaluation.break_even_tariff
    assert evaluation.recommended_bid_ceiling <= evaluation.maximum_acceptable_service_tariff


def test_storage_value_breakdown_is_non_zero_when_prices_vary():
    base_objects = [
        _main(),
        _mini("gen-mini", district="gen_west"),
        EnergyObject(
            object_id="owned-solar",
            object_type_id=25,
            code="solar",
            name="owned-solar",
            category="generator",
            district="gen_west",
            parameters={
                "contract_rub_per_tick": 2.0,
                "generation_mw": 20.0,
                "efficiency": 0.95,
                "connection_point": "A",
            },
            parent_id="gen-mini",
        ),
        EnergyObject(
            object_id="owned-office",
            object_type_id=26,
            code="office",
            name="owned-office",
            category="consumer",
            district="load_north",
            parameters={
                "tariff_rub_per_tick": 12.0,
                "tariff_rub_per_mw_tick": 12.0,
                "expected_consumption_mw": 4.0,
                "elasticity": 0.05,
                "connection_point": "A",
            },
            parent_id="gen-mini",
        ),
    ]
    evaluation = evaluate_candidate_bundle(
        lot_id=3,
        lot_name="Накопитель",
        base_objects=base_objects,
        candidate_objects=[_storage("storage-1", district="gen_west", parent_id="gen-mini", contract=1.5)],
        forecast_pack=_forecast_pack(sell_price=9.5, buy_price=9.4),
        ruleset_config=build_default_ruleset_config(),
    )

    total_storage_value = (
        evaluation.storage_value.arbitrage
        + evaluation.storage_value.balancing
        + evaluation.storage_value.reserve
        + evaluation.storage_value.anti_dumping_support
    )
    assert total_storage_value > 0.0
    assert evaluation.expected_delta_profit > 0.0


def test_network_validator_requires_two_inputs_for_hospital():
    report = validate_network(
        [
            _main(),
            _mini("load-mini", district="load_north"),
            EnergyObject(
                object_id="hospital-1",
                object_type_id=40,
                code="hospital",
                name="hospital-1",
                category="consumer",
                district="load_north",
                parameters={
                    "tariff_rub_per_tick": 12.0,
                    "tariff_rub_per_mw_tick": 12.0,
                    "expected_consumption_mw": 5.0,
                    "connection_point": "A",
                },
                parent_id="load-mini",
            ),
        ]
    )

    assert any(issue.code == "HOSPITAL_REQUIRES_TWO_INPUTS" for issue in report.issues)


def test_losses_can_turn_consumer_from_profitable_to_unprofitable():
    base_objects = [_main(), _mini("load-mini", district="load_north")]
    config = build_default_ruleset_config()
    profitable = evaluate_candidate_bundle(
        lot_id=4,
        lot_name="Дом A рядом",
        base_objects=base_objects,
        candidate_objects=[
            _consumer(
                "house-near",
                "house_a",
                district="load_north",
                parent_id="load-mini",
                tariff=39.0,
                demand=4.0,
                connection_point="A",
            )
        ],
        forecast_pack=_forecast_pack(buy_price=8.4),
        ruleset_config=config,
    )
    unprofitable = evaluate_candidate_bundle(
        lot_id=5,
        lot_name="Дом A с потерями",
        base_objects=base_objects,
        candidate_objects=[
            _consumer(
                "house-far",
                "house_a",
                district="load_north",
                parent_id="load-mini",
                tariff=39.0,
                demand=4.0,
                connection_point="D",
            )
        ],
        forecast_pack=_forecast_pack(buy_price=8.4),
        ruleset_config=config,
    )

    assert profitable.expected_delta_profit > 0.0
    assert unprofitable.expected_delta_profit < profitable.expected_delta_profit
    assert unprofitable.expected_delta_profit < 0.0


def test_infrastructure_can_have_positive_enabler_value():
    evaluation = evaluate_candidate_bundle(
        lot_id=6,
        lot_name="Мини-подстанция генерации",
        base_objects=[
            _main(ports=2),
            _mini("load-mini", district="load_north", ports=1),
            EnergyObject(
                object_id="owned-house",
                object_type_id=11,
                code="house_a",
                name="owned-house",
                category="consumer",
                district="load_north",
                parameters={
                    "tariff_rub_per_tick": 11.0,
                    "tariff_rub_per_mw_tick": 11.0,
                    "expected_consumption_mw": 4.2,
                    "connection_point": "A",
                },
                parent_id="load-mini",
            ),
        ],
        candidate_objects=[_mini("gen-mini-candidate", district="gen_east", ports=4)],
        forecast_pack=_forecast_pack(sell_price=10.5),
        ruleset_config=build_default_ruleset_config(),
        follow_up_candidates=[
            (
                101,
                "Генераторный пакет",
                [
                    _solar("future-solar", district="gen_east", parent_id="", contract=1.0, generation=30.0),
                    _wind("future-wind", district="gen_east", parent_id="", contract=1.0, channel="wind_west"),
                ],
            )
        ],
    )

    assert evaluation.enabler_value > 0.0
    assert any("открывает лот" in note.lower() for note in evaluation.synergy_notes)


def test_factory_single_input_is_warning_not_blocking_error():
    report = validate_network(
        [
            _main(),
            _mini("load-mini", district="load_north"),
            EnergyObject(
                object_id="factory-1",
                object_type_id=41,
                code="factory",
                name="factory-1",
                category="consumer",
                district="load_north",
                parameters={
                    "tariff_rub_per_tick": 11.0,
                    "tariff_rub_per_mw_tick": 11.0,
                    "expected_consumption_mw": 10.0,
                    "connection_point": "A",
                },
                parent_id="load-mini",
            ),
        ]
    )

    assert any(issue.code == "FACTORY_SINGLE_INPUT" for issue in report.issues)
    assert not any(issue.code == "FACTORY_NOT_CONNECTED" for issue in report.issues)


def test_consumer_fixed_tariff_revenue_is_per_tick_not_per_delivered_mw():
    config = build_default_ruleset_config()
    forecast = _forecast_pack(buy_price=8.1)
    base = [_main(), _mini("load-mini", district="load_north")]
    low_demand = simulate_system(
        objects=base + [
            _consumer(
                "house-low",
                "house_a",
                district="load_north",
                parent_id="load-mini",
                tariff=9.0,
                demand=1.2,
            )
        ],
        forecast_pack=forecast,
        ruleset_config=config,
    )
    high_demand = simulate_system(
        objects=base + [
            _consumer(
                "factory-high",
                "factory",
                district="load_north",
                parent_id="load-mini",
                tariff=9.0,
                demand=9.5,
            )
        ],
        forecast_pack=forecast,
        ruleset_config=config,
    )

    assert low_demand.totals.fixed_tariff_revenue == high_demand.totals.fixed_tariff_revenue
    assert high_demand.totals.gp_purchase_cost > low_demand.totals.gp_purchase_cost


def test_anti_dumping_formula_uses_previous_useful_energy():
    config = build_default_ruleset_config()

    assert anti_dumping_cap_mw(0.0, config) == 10.0
    assert anti_dumping_cap_mw(25.0, config) == 40.0
    assert anti_dumping_cap_mw(60.0, config) == 82.0


def test_useful_energy_is_lower_than_gross_generation_when_losses_apply():
    config = build_default_ruleset_config()
    result = simulate_system(
        objects=[
            _main(),
            _mini("gen-mini", district="gen_west"),
            _solar(
                "solar-far",
                district="gen_west",
                parent_id="gen-mini",
                contract=1.0,
                generation=20.0,
                connection_point="D",
            ),
        ],
        forecast_pack=_forecast_pack(sell_price=10.5),
        ruleset_config=config,
    )

    assert result.totals.total_generation_mw > result.totals.useful_generation_mw
    assert result.totals.loss_cost > 0.0


def test_no_main_substation_is_blocking_error():
    report = validate_network([_mini("orphan-mini", district="load_north", parent_id=None)])

    assert report.blocking is True
    assert any(issue.code == "NO_MAIN_SUBSTATION" for issue in report.issues)


def test_mini_substation_purchased_but_not_installed_is_blocking_error():
    report = validate_network(
        [
            _main(),
            EnergyObject(
                object_id="mini-lot",
                object_type_id=2,
                code="mini_substation",
                name="mini-lot",
                category="infrastructure",
                district="load_north",
                parameters={"ports": 4},
                source_lot_id=77,
                parent_id=None,
            ),
        ]
    )

    assert report.blocking is True
    assert any(issue.code == "MINI_SUBSTATION_NOT_INSTALLED" for issue in report.issues)


def test_bundle_value_is_not_equal_to_sum_of_standalone_values_when_infra_enables_layout():
    config = build_default_ruleset_config()
    base = [_main(ports=2)]
    mini = _mini("bundle-mini", district="gen_east", parent_id="main", ports=4)
    solar = _solar("bundle-solar", district="gen_east", parent_id="bundle-mini", contract=1.2, generation=20.0)

    mini_only = evaluate_candidate_bundle(
        lot_id=10,
        lot_name="Mini only",
        base_objects=base,
        candidate_objects=[mini],
        forecast_pack=_forecast_pack(sell_price=9.9),
        ruleset_config=config,
    )
    solar_only = evaluate_candidate_bundle(
        lot_id=11,
        lot_name="Solar only",
        base_objects=base,
        candidate_objects=[solar],
        forecast_pack=_forecast_pack(sell_price=9.9),
        ruleset_config=config,
    )
    bundle = evaluate_candidate_bundle(
        lot_id=12,
        lot_name="Mini + Solar",
        base_objects=base,
        candidate_objects=[mini, solar],
        forecast_pack=_forecast_pack(sell_price=9.9),
        ruleset_config=config,
    )

    assert bundle.bundle_synergy_value != 0.0
    assert bundle.expected_delta_profit != pytest.approx(
        mini_only.expected_delta_profit + solar_only.expected_delta_profit
    )
