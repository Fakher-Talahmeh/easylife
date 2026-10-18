"""Guards docs/API_DOCUMENTATION.md: every real endpoint must be documented, and nothing
documented may be missing from the code. Heading format: `#### METHOD /path` (relative to /api/v1).
"""

import re
from pathlib import Path

from django.urls import URLPattern, URLResolver, get_resolver

DOC = Path(__file__).resolve().parents[3] / "docs" / "API_DOCUMENTATION.md"
PREFIX = "api/v1/"
IGNORED_METHODS = {"HEAD", "OPTIONS"}


def _clean(piece):
    piece = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", piece)  # regex group -> {name}
    piece = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", piece)  # <uuid:uuid> -> {uuid}
    return piece.lstrip("^").rstrip("$")


def _walk(patterns, prefix=""):
    for p in patterns:
        piece = _clean(str(p.pattern))
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + piece)
        elif isinstance(p, URLPattern):
            yield prefix + piece, p.callback


def _methods(callback):
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    allowed = {m.upper() for m in cls.http_method_names}  # a view may disable methods its router maps
    actions = getattr(callback, "actions", None)  # ViewSet routes
    if actions:
        found = {m.upper() for m in actions}
    else:
        found = {m.upper() for m in cls.http_method_names if hasattr(cls, m)}
    return (found & allowed) - IGNORED_METHODS


def actual_endpoints():
    out = set()
    for path, callback in _walk(get_resolver().url_patterns):
        if not path.startswith(PREFIX):
            continue
        if "format" in path or getattr(callback, "cls", None).__name__ == "APIRootView":
            continue  # DRF format-suffix variants and the browsable root
        rel = "/" + path[len(PREFIX):]
        for method in _methods(callback):
            out.add(f"{method} {rel}")
    return out


def documented_endpoints():
    text = DOC.read_text(encoding="utf8")
    return {
        f"{m} {p}"
        for m, p in re.findall(r"^####\s+(GET|POST|PUT|PATCH|DELETE)\s+(\S+)\s*$", text, flags=re.M)
    }


def test_every_endpoint_is_documented_and_nothing_stale():
    actual, documented = actual_endpoints(), documented_endpoints()
    assert actual, "no endpoints discovered - route discovery is broken"
    missing = sorted(actual - documented)
    stale = sorted(documented - actual)
    assert not missing, (
        "Endpoints missing from docs/API_DOCUMENTATION.md (add `#### METHOD /path` sections):\n  "
        + "\n  ".join(missing)
    )
    assert not stale, (
        "Documented in docs/API_DOCUMENTATION.md but not found in the code:\n  " + "\n  ".join(stale)
    )
