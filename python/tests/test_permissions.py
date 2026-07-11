from types import SimpleNamespace

from django.test import override_settings
from rest_framework.test import APIRequestFactory

from drf_foundation.permissions import (
    IsAdminUserOrTaskKey,
    IsAuthenticatedOrTaskKey,
    request_has_valid_task_key,
)

factory = APIRequestFactory()


def _request_with_key(key: str | None):
    headers = {"HTTP_X_TASK_KEY": key} if key is not None else {}
    return factory.get("/x", **headers)


def _user(is_authenticated: bool, is_staff: bool = False):
    return SimpleNamespace(is_authenticated=is_authenticated, is_staff=is_staff)


def test_valid_task_key_passes():
    request = _request_with_key("test-task-key")
    assert request_has_valid_task_key(request)


def test_wrong_task_key_fails():
    request = _request_with_key("wrong-key")
    assert not request_has_valid_task_key(request)


def test_missing_task_key_fails():
    request = _request_with_key(None)
    assert not request_has_valid_task_key(request)


@override_settings(TASK_TRIGGER_KEY="")
def test_blank_configured_key_never_matches_blank_header():
    request = _request_with_key("")
    assert not request_has_valid_task_key(request)


def test_is_admin_user_or_task_key_allows_staff():
    request = _request_with_key(None)
    request.user = _user(is_authenticated=True, is_staff=True)
    assert IsAdminUserOrTaskKey().has_permission(request, view=None)


def test_is_admin_user_or_task_key_rejects_non_staff_without_key():
    request = _request_with_key(None)
    request.user = _user(is_authenticated=True, is_staff=False)
    assert not IsAdminUserOrTaskKey().has_permission(request, view=None)


def test_is_admin_user_or_task_key_allows_valid_key_without_user():
    request = _request_with_key("test-task-key")
    request.user = _user(is_authenticated=False)
    assert IsAdminUserOrTaskKey().has_permission(request, view=None)


def test_is_authenticated_or_task_key_allows_any_authenticated_user():
    request = _request_with_key(None)
    request.user = _user(is_authenticated=True, is_staff=False)
    assert IsAuthenticatedOrTaskKey().has_permission(request, view=None)


def test_is_authenticated_or_task_key_allows_valid_key_without_user():
    request = _request_with_key("test-task-key")
    request.user = _user(is_authenticated=False)
    assert IsAuthenticatedOrTaskKey().has_permission(request, view=None)
