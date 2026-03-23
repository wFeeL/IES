# Post-Auction System Template 2026

`post_auction_system_plan_2026.yaml` нужен для перехода от результата аукциона к проверяемой модели энергосистемы.

## Где используется

- `GET /api/sessions/<id>/post-auction-plan`
- `GET /api/sessions/<id>/post-auction-plan.yaml`
- `resources/templates/post_auction_system_plan_2026.yaml`

## Что содержит шаблон

### `game`

- `ruleset`
- `horizon_ticks`
- `selected_forecast`
- `assumptions_versions`

### `auction_result`

- `won_lots`
- `dropped_lots`
- `available_second_round_opportunities`
- `all_pay_spend_used`
- `remaining_strategic_budget`

### `inventory`

- `main_substation`
- `mini_substations`
- `generators`
- `storages`
- `consumers`

### `topology_candidates`

Для каждого candidate:
- `candidate_id`
- `edge_list`
- `district_map`
- `validation_block`
- `expected_losses`
- `mandatory_fixes`

### `installation_priority`

- priority order для `global_solar`, `global_wind`, `local_wind`, `local_solar`;
- placement notes;
- blocked-by-higher-priority flags.

### `object_models`

- отдельные блоки для `solar`, `wind`, `consumer`, `storage`.

### `market_plan`

- `declared_sale_per_tick`
- `anti_dumping_cap_per_tick`
- `reserve_policy`
- `balancing_reserve`
- `pricing_policy`

### `tick_model`

По каждому такту:
- `gross_generation`
- `gross_demand`
- `losses`
- `useful_energy`
- `declared_sale`
- `realized_sale`
- `gp_sale`
- `purchase_from_gp`
- `balancing_penalty`
- `fixed_tariffs`
- `service_tariffs`
- `tick_profit`

### `final_decision`

- `selected_topology`
- `selected_market_policy`
- `selected_storage_policy`
- `critical_checks`
- `top_risks`
- `first_fixes`

## Как интерпретировать

- шаблон не подменяет domain engine и не содержит отдельной логики расчёта;
- YAML строится из текущей session state и того же `domain/ies2026` engine;
- topology candidates надо читать как shortlist допустимых схем, а не как окончательный автопилот;
- если в `critical_checks` есть ошибки, модель не считается готовой к запуску.

## Что в нём аппроксимационное

- demand elasticity;
- loss model;
- market clearing approximation;
- storage reserve policy.

Это оставлено параметризуемым и должно калиброваться реальными игровыми данными.
