import re

from drf_foundation.settings_helpers import (
    allowed_hosts_from_env,
    pooled_database,
    production_security_settings,
    simple_jwt_defaults,
)


def test_pooled_database_from_url():
    config = pooled_database(env={"DATABASE_URL": "postgresql://u:p@db.internal:5432/app"})
    assert config["HOST"] == "db.internal"
    assert config["OPTIONS"]["pool"] == {"min_size": 1, "max_size": 5, "timeout": 10}


def test_pooled_database_from_postgres_vars():
    config = pooled_database(default_name="myapp", env={"POSTGRES_PORT": "5433"})
    assert config["NAME"] == "myapp"
    assert config["PORT"] == "5433"
    assert config["OPTIONS"]["pool"]["max_size"] == 5


def test_pooled_database_bounds_are_tunable():
    config = pooled_database(max_size=10, env={})
    assert config["OPTIONS"]["pool"]["max_size"] == 10


def test_production_security_settings_exempts_the_health_path():
    settings = production_security_settings()
    assert settings["SECURE_SSL_REDIRECT"] is True
    (pattern,) = settings["SECURE_REDIRECT_EXEMPT"]
    # Matched against the path with the leading slash stripped.
    assert re.match(pattern, "api/health")
    assert not re.match(pattern, "api/anything-else")


def test_production_security_settings_satisfy_the_core_check():
    settings = production_security_settings()
    assert settings["SECURE_HSTS_SECONDS"] >= 31536000
    assert settings["SESSION_COOKIE_SECURE"] is True


def test_allowed_hosts_include_healthcheck_prober():
    hosts = allowed_hosts_from_env(env={"ALLOWED_HOSTS": "api.example.com"})
    assert hosts == ["api.example.com", "healthcheck.railway.app"]


def test_allowed_hosts_append_railway_domain():
    hosts = allowed_hosts_from_env(env={"RAILWAY_PUBLIC_DOMAIN": "x.up.railway.app"})
    assert "x.up.railway.app" in hosts


def test_simple_jwt_defaults_rotate_and_blacklist():
    config = simple_jwt_defaults()
    assert config["ROTATE_REFRESH_TOKENS"] is True
    assert config["BLACKLIST_AFTER_ROTATION"] is True
