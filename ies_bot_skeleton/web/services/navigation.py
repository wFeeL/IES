from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from flask import Request, request, url_for

BreadcrumbItem = Dict[str, Any]


def is_safe_internal_url(target: str, req: Optional[Request] = None) -> bool:
    if not target:
        return False
    req_obj = req or request
    ref_url = urlparse(req_obj.host_url)
    test_url = urlparse(urljoin(req_obj.host_url, target))
    return test_url.scheme in {"http", "https"} and ref_url.netloc == test_url.netloc


def safe_back_url(
    *,
    fallback_endpoint: str = "pages.dashboard",
    req: Optional[Request] = None,
    **fallback_values: Any,
) -> str:
    req_obj = req or request
    ref = req_obj.referrer or ""
    if ref and is_safe_internal_url(ref, req_obj):
        return ref
    return url_for(fallback_endpoint, **fallback_values)


def safe_next_url(
    *,
    fallback_endpoint: str = "pages.dashboard",
    req: Optional[Request] = None,
    **fallback_values: Any,
) -> str:
    req_obj = req or request
    target = (req_obj.values.get("next") or "").strip()
    if target and is_safe_internal_url(target, req_obj):
        return target
    return url_for(fallback_endpoint, **fallback_values)


def build_breadcrumbs(
    items: Iterable[Tuple[str, Optional[str], Optional[Dict[str, Any]]]],
) -> List[BreadcrumbItem]:
    out: List[BreadcrumbItem] = []
    prepared = list(items)
    for idx, (label, endpoint, values) in enumerate(prepared):
        is_last = idx == len(prepared) - 1
        url = None
        if endpoint and not is_last:
            url = url_for(endpoint, **(values or {}))
        out.append({"label": label, "url": url, "is_current": is_last})
    return out
