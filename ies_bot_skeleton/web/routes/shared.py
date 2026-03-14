from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import Blueprint

ViewFunc = Callable[..., Any]


@dataclass(frozen=True)
class _RouteDef:
    rule: str
    view_func: ViewFunc
    options: dict[str, Any]


@dataclass(frozen=True)
class _ErrorHandlerDef:
    exc_class: type[BaseException]
    handler: ViewFunc


class DeferredBlueprint:
    def __init__(self, name: str, import_name: str, **kwargs: Any) -> None:
        self.name = name
        self.import_name = import_name
        self.kwargs = dict(kwargs)
        self._routes: list[_RouteDef] = []
        self._error_handlers: list[_ErrorHandlerDef] = []

    def route(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        def decorator(fn: ViewFunc) -> ViewFunc:
            self._routes.append(_RouteDef(rule=rule, view_func=fn, options=dict(options)))
            return fn

        return decorator

    def get(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["GET"], **options)

    def post(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["POST"], **options)

    def put(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["PUT"], **options)

    def delete(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["DELETE"], **options)

    def errorhandler(self, exc_class: type[BaseException]) -> Callable[[ViewFunc], ViewFunc]:
        def decorator(fn: ViewFunc) -> ViewFunc:
            self._error_handlers.append(_ErrorHandlerDef(exc_class=exc_class, handler=fn))
            return fn

        return decorator

    def materialize(self) -> Blueprint:
        bp = Blueprint(self.name, self.import_name, **self.kwargs)
        for route in self._routes:
            opts = dict(route.options)
            endpoint = opts.pop("endpoint", None)
            bp.add_url_rule(route.rule, endpoint=endpoint, view_func=route.view_func, **opts)
        for handler in self._error_handlers:
            bp.register_error_handler(handler.exc_class, handler.handler)
        return bp


pages_bp = DeferredBlueprint("pages", __name__)
api_bp = DeferredBlueprint("api", __name__, url_prefix="/api")
