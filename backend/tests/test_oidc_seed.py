from __future__ import annotations

from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.auth
from app.auth.oidc_seed import seed_oidc_provider
from app.core.crypto import decrypt, is_encrypted
from app.core.db import Base
from app.models.auth import IdentityProvider


async def _session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'seed-oidc.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_oidc_seed_creates_single_provider_and_encrypts_secret(tmp_path, monkeypatch):
    engine, Session = await _session(tmp_path)
    settings = SimpleNamespace(
        sso_oidc_enabled=True,
        sso_oidc_display_name="MicroAge Sign-in",
        sso_oidc_issuer="https://login.microsoftonline.com/tenant-id/v2.0",
        sso_oidc_client_id="client-1",
        sso_oidc_client_secret="secret-1",
        sso_oidc_scopes="openid email profile",
        sso_oidc_group_claim="groups",
    )
    monkeypatch.setattr("app.auth.oidc_seed.get_settings", lambda: settings)

    async with Session() as db:
        await seed_oidc_provider(db)
        await seed_oidc_provider(db)
        rows = (
            await db.execute(
                sa.select(IdentityProvider).where(IdentityProvider.type == "oidc")
            )
        ).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.name == "MicroAge Sign-in"
    assert row.enabled is True
    assert row.button_label == "MicroAge Sign-in"
    assert row.config_json["issuer"] == "https://login.microsoftonline.com/tenant-id/v2.0"
    assert row.config_json["client_id"] == "client-1"
    assert row.config_json["scopes"] == "openid email profile"
    assert row.config_json["group_claim"] == "groups"
    assert is_encrypted(row.config_json["client_secret"])
    assert decrypt(row.config_json["client_secret"]) == "secret-1"
    await engine.dispose()


async def test_oidc_seed_updates_existing_provider_by_issuer(tmp_path, monkeypatch):
    engine, Session = await _session(tmp_path)
    settings = SimpleNamespace(
        sso_oidc_enabled=True,
        sso_oidc_display_name="MicroAge Sign-in",
        sso_oidc_issuer="https://login.microsoftonline.com/tenant-id/v2.0",
        sso_oidc_client_id="client-1",
        sso_oidc_client_secret="secret-1",
        sso_oidc_scopes="openid email profile",
        sso_oidc_group_claim="groups",
    )
    monkeypatch.setattr("app.auth.oidc_seed.get_settings", lambda: settings)

    async with Session() as db:
        await seed_oidc_provider(db)
        settings.sso_oidc_display_name = "MicroAge Global Login"
        settings.sso_oidc_client_id = "client-2"
        settings.sso_oidc_client_secret = "secret-2"
        await seed_oidc_provider(db)
        rows = (
            await db.execute(
                sa.select(IdentityProvider).where(IdentityProvider.type == "oidc")
            )
        ).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.name == "MicroAge Global Login"
    assert row.button_label == "MicroAge Global Login"
    assert row.config_json["client_id"] == "client-2"
    assert decrypt(row.config_json["client_secret"]) == "secret-2"
    await engine.dispose()
