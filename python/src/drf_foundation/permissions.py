"""Shared API permission helpers.

The intended posture is **deny-by-default**: set
``REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = ["rest_framework.permissions.IsAuthenticated"]``
so every endpoint requires authentication unless it is *explicitly* opened. Open the
intentional public surface with :func:`public_endpoint`. Grepping for that decorator then
gives the complete, auditable allowlist of routes an anonymous caller can reach — there is
no other way for a view to be public, so nothing leaks by forgetting a decorator.
"""

import hmac
from collections.abc import Callable

from django.conf import settings
from rest_framework.decorators import permission_classes
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView


def public_endpoint(view_func: Callable) -> Callable:
    """Mark a DRF function-based view as intentionally public (no auth required).

    Apply it directly beneath ``@api_view`` (closest to the function)::

        @api_view(["GET"])
        @public_endpoint
        def my_view(request): ...

    This is a thin, self-documenting wrapper over ``permission_classes([AllowAny])``;
    its purpose is to make "this is public on purpose" greppable and obvious at the
    call site, rather than scattering bare ``AllowAny`` across the codebase.
    """
    return permission_classes([AllowAny])(view_func)


def task_trigger_key() -> str:
    """The configured shared ops secret (empty string when unset).

    Reads ``settings.TASK_TRIGGER_KEY``. Leave it unset/blank in any environment where
    the shared-key tier should be fully disabled — :func:`request_has_valid_task_key`
    always returns ``False`` when it's blank, so a blank env var can never authorize a
    blank header.
    """
    return getattr(settings, "TASK_TRIGGER_KEY", "") or ""


def request_has_valid_task_key(request: Request) -> bool:
    """Constant-time check of the ``X-Task-Key`` header against the shared secret.

    Returns ``False`` when no key is configured. This is the single source of truth for
    the shared-key comparison — reuse it everywhere a task-key check is needed instead of
    writing a second comparison.
    """
    configured = task_trigger_key()
    if not configured:
        return False
    provided = request.headers.get("X-Task-Key", "")
    return hmac.compare_digest(provided, configured)


class IsAdminUserOrTaskKey(BasePermission):
    """Allow a staff user (admin JWT/session) **or** a valid shared ``X-Task-Key``.

    Lets headless ops tooling (curl, cron, an agent) drive admin actions with the shared
    key instead of a minted admin credential, while a human dashboard keeps using auth
    normally. The key's blast radius is everything it gates — scope it to ops-level
    actions, not endpoints that read user content.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff:
            return True
        return request_has_valid_task_key(request)


class IsAuthenticatedOrTaskKey(BasePermission):
    """Allow any authenticated user **or** a valid shared ``X-Task-Key``.

    Additive over the default ``IsAuthenticated`` gate: preserves normal logged-in
    access while also letting headless ops tooling read an endpoint with the shared key
    — same trust tier as :class:`IsAdminUserOrTaskKey` — without minting a credential.
    Use it on read endpoints ops needs to spot-check; for mutations use
    :class:`IsAdminUserOrTaskKey` instead.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return True
        return request_has_valid_task_key(request)
