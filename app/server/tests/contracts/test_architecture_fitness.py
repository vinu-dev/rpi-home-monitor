"""Architecture fitness functions — enforce ADR constraints via static AST analysis.

These tests parse Python source files and assert structural invariants that
prevent architectural decay. They are fast (no server, no I/O) and fail CI
immediately when a violation is introduced.

Constraints enforced:
  1. API layer never imports or calls subprocess (delegates to services).
  2. Every mutating route requiring admin_required also has csrf_protect.
  3. Every mutating route requiring login_required also has csrf_protect.
  4. Camera M2M endpoints call _verify_camera_hmac (not session auth).
  5. Camera M2M endpoints are not behind session-auth decorators.
  6. All public domain model classes use @dataclass.
  7. API modules never instantiate Store() directly (use current_app.store).
"""

import ast
from pathlib import Path

import pytest

MONITOR_ROOT = Path(__file__).parents[2] / "monitor"
API_ROOT = MONITOR_ROOT / "api"

_MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})
_M2M_ROUTE_NAMES = frozenset({"config_notify", "camera_goodbye", "camera_heartbeat"})
_SESSION_AUTH_DECORATORS = frozenset(
    {"login_required", "admin_required", "viewer_or_better"}
)

# WHEP is a protocol endpoint (WebRTC signaling), not a form-POST mutation.
# application/sdp Content-Type forces a CORS preflight on every cross-origin
# request, so CORS headers already prevent the cross-origin CSRF attack vector.
# Adding @csrf_protect would break WHEP clients that don't send the token.
_CSRF_EXEMPT_ROUTES = frozenset({"whep_proxy"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    names = []
    for d in node.decorator_list:
        func = d.func if isinstance(d, ast.Call) else d
        if isinstance(func, ast.Name):
            names.append(func.id)
        elif isinstance(func, ast.Attribute):
            names.append(func.attr)
    return names


def _route_http_methods(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    for d in node.decorator_list:
        if not isinstance(d, ast.Call):
            continue
        func = d.func
        if not (isinstance(func, ast.Attribute) and func.attr == "route"):
            continue
        for kw in d.keywords:
            if kw.arg == "methods" and isinstance(kw.value, ast.List):
                return {
                    e.value.upper()
                    for e in kw.value.elts
                    if isinstance(e, ast.Constant)
                }
    return {"GET"}


def _route_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            func = d.func if isinstance(d, ast.Call) else d
            if isinstance(func, ast.Attribute) and func.attr == "route":
                result.append(node)
                break
    return result


def _api_files() -> list[Path]:
    return sorted(API_ROOT.glob("*.py"))


# ---------------------------------------------------------------------------
# 1. No subprocess in API layer
# ---------------------------------------------------------------------------


class TestNoSubprocessInApiLayer:
    @pytest.mark.parametrize("path", _api_files())
    def test_no_subprocess_import(self, path: Path):
        tree = _parse(path)
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "subprocess" in alias.name:
                        violations.append(f"line {node.lineno}: import {alias.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "subprocess" in node.module
            ):
                violations.append(f"line {node.lineno}: from {node.module} import ...")
        assert not violations, (
            f"{path.name} imports subprocess.\n"
            "API layer must delegate process execution to service classes.\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# 2 & 3. CSRF protection on all mutating auth-required routes
# ---------------------------------------------------------------------------


class TestCsrfOnMutatingRoutes:
    @pytest.mark.parametrize("path", _api_files())
    def test_admin_mutating_routes_have_csrf(self, path: Path):
        tree = _parse(path)
        violations = []
        for fn in _route_functions(tree):
            if not _route_http_methods(fn) & _MUTATING_METHODS:
                continue
            dec = _decorator_names(fn)
            if "admin_required" in dec and "csrf_protect" not in dec:
                violations.append(f"  {fn.name} (line {fn.lineno})")
        assert not violations, (
            f"{path.name}: mutating @admin_required routes missing @csrf_protect:\n"
            + "\n".join(violations)
        )

    @pytest.mark.parametrize("path", _api_files())
    def test_login_mutating_routes_have_csrf(self, path: Path):
        tree = _parse(path)
        violations = []
        for fn in _route_functions(tree):
            if fn.name in _CSRF_EXEMPT_ROUTES:
                continue
            if not _route_http_methods(fn) & _MUTATING_METHODS:
                continue
            dec = _decorator_names(fn)
            if "login_required" in dec and "csrf_protect" not in dec:
                violations.append(f"  {fn.name} (line {fn.lineno})")
        assert not violations, (
            f"{path.name}: mutating @login_required routes missing @csrf_protect:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# 4 & 5. Camera M2M HMAC authentication
# ---------------------------------------------------------------------------


class TestCameraM2MAuthentication:
    def test_m2m_endpoints_call_verify_hmac(self):
        tree = _parse(API_ROOT / "cameras.py")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in _M2M_ROUTE_NAMES:
                continue
            hmac_calls = [
                n
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_verify_camera_hmac"
            ]
            assert hmac_calls, (
                f"cameras.py:{node.lineno} — {node.name} is a camera M2M endpoint "
                "but does not call _verify_camera_hmac. "
                "All camera-to-server routes must verify HMAC to prevent spoofing."
            )

    def test_m2m_endpoints_not_behind_session_auth(self):
        tree = _parse(API_ROOT / "cameras.py")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in _M2M_ROUTE_NAMES:
                continue
            dec = set(_decorator_names(node))
            found = dec & _SESSION_AUTH_DECORATORS
            assert not found, (
                f"cameras.py:{node.lineno} — {node.name} is a camera M2M endpoint "
                f"but has session auth decorator(s): {sorted(found)}. "
                "M2M endpoints authenticate via HMAC, not browser sessions."
            )


# ---------------------------------------------------------------------------
# 6. Domain models must be dataclasses
# ---------------------------------------------------------------------------


class TestModelDataclasses:
    def test_public_model_classes_are_dataclasses(self):
        tree = _parse(MONITOR_ROOT / "models.py")
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.startswith("_"):
                continue
            dec_names = [
                (d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list
            ]
            flat = []
            for d in dec_names:
                if isinstance(d, ast.Name):
                    flat.append(d.id)
                elif isinstance(d, ast.Attribute):
                    flat.append(d.attr)
            if "dataclass" not in flat:
                violations.append(f"  {node.name} (line {node.lineno})")
        assert not violations, (
            "models.py has non-dataclass public types.\n"
            "All domain models must use @dataclass for consistent serialization.\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# 7. API layer never instantiates Store() directly
# ---------------------------------------------------------------------------


class TestApiLayerStoreAccess:
    @pytest.mark.parametrize("path", _api_files())
    def test_no_direct_store_import(self, path: Path):
        tree = _parse(path)
        violations = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "store" in node.module.lower()
            ):
                imported = [a.name for a in node.names]
                if "Store" in imported:
                    violations.append(
                        f"line {node.lineno}: imports Store from {node.module}"
                    )
        assert not violations, (
            f"{path.name} imports Store directly.\n"
            "API modules must access the store via current_app.store.\n"
            + "\n".join(violations)
        )
