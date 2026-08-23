"""API-key mechanics: hashing, resolution, revocation, and the last-used rate cap."""

from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from django.test import RequestFactory

from drf_foundation.mcp.api_keys import TokenCodec, bearer_token, resolve_token
from tests.testapp.models import Account, ApiKey

CODEC = TokenCodec(prefix="tk_")


@pytest.fixture
def account(db):
    return Account.objects.create(name="Acme")


def mint(account, codec=CODEC, **kwargs):
    token = codec.generate()
    key = ApiKey.objects.create(
        account=account,
        name=kwargs.pop("name", "a key"),
        key_hash=codec.hash(token),
        key_prefix=codec.display_prefix(token),
        **kwargs,
    )
    return token, key


def test_generated_tokens_carry_the_prefix_and_are_unguessable():
    tokens = {CODEC.generate() for _ in range(50)}
    assert len(tokens) == 50, "every mint must produce a distinct secret"
    for token in tokens:
        assert token.startswith("tk_")
        assert len(token) == len("tk_") + 43
        assert CODEC.owns(token)


def test_only_the_hash_is_stored(account):
    token, key = mint(account)
    assert key.key_hash == CODEC.hash(token)
    assert token not in key.key_hash
    # The visible handle is a prefix of the secret, not the secret.
    assert key.key_prefix == token[:12]
    assert len(key.key_prefix) < len(token)


def test_resolve_finds_an_active_key_and_ignores_foreign_prefixes(account):
    token, key = mint(account)
    assert resolve_token(ApiKey, CODEC, token) == key
    # A token belonging to some other credential system is not ours to resolve —
    # claimed-by-prefix is what keeps two auth classes from fighting over a header.
    assert resolve_token(ApiKey, CODEC, "other_" + token) is None
    assert resolve_token(ApiKey, CODEC, CODEC.generate()) is None


def test_a_revoked_key_stops_resolving(account):
    from django.utils import timezone

    token, key = mint(account)
    key.revoked_at = timezone.now()
    key.save(update_fields=["revoked_at"])

    assert resolve_token(ApiKey, CODEC, token) is None
    assert not ApiKey.objects.get(pk=key.pk).is_active


def test_last_used_is_written_once_per_window_not_once_per_request(account):
    # last_used_at is advisory UI data. Without the cap, every read through a
    # key-authenticated endpoint becomes a write.
    token, _ = mint(account)
    start = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)

    with time_machine.travel(start, tick=False):
        first = resolve_token(ApiKey, CODEC, token)
        assert first.last_used_at == start

    # Well inside the window: the stamp must not move.
    with time_machine.travel(start + timedelta(seconds=30), tick=False):
        resolve_token(ApiKey, CODEC, token)
    assert ApiKey.objects.get(pk=first.pk).last_used_at == start

    # Past it: one write, and the in-memory instance agrees with the row.
    later = start + timedelta(minutes=5)
    with time_machine.travel(later, tick=False):
        refreshed = resolve_token(ApiKey, CODEC, token)
    assert refreshed.last_used_at == later
    assert ApiKey.objects.get(pk=first.pk).last_used_at == later


def test_bearer_token_reads_only_a_well_formed_bearer_header():
    factory = RequestFactory()
    assert bearer_token(factory.get("/", HTTP_AUTHORIZATION="Bearer tk_abc")) == "tk_abc"
    # Case-insensitive scheme, per RFC 7235.
    assert bearer_token(factory.get("/", HTTP_AUTHORIZATION="bearer tk_abc")) == "tk_abc"
    assert bearer_token(factory.get("/")) is None
    assert bearer_token(factory.get("/", HTTP_AUTHORIZATION="Basic abc")) is None
    assert bearer_token(factory.get("/", HTTP_AUTHORIZATION="Bearer")) is None
    assert bearer_token(factory.get("/", HTTP_AUTHORIZATION="Bearer a b")) is None
