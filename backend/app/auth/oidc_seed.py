"""Startup seed for an env-driven OIDC identity provider."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.users import _merge_idp_config
from app.core.config import get_settings
from app.models.auth import IdentityProvider

log = logging.getLogger("app.auth.oidc_seed")


def _normalize(text: str) -> str:
    return (text or "").strip()


async def seed_oidc_provider(db: AsyncSession) -> None:
    settings = get_settings()
    if not settings.sso_oidc_enabled:
        return

    issuer = _normalize(settings.sso_oidc_issuer)
    client_id = _normalize(settings.sso_oidc_client_id)
    client_secret = _normalize(settings.sso_oidc_client_secret)
    if not (issuer and client_id and client_secret):
        log.warning(
            "SSO_OIDC_ENABLED is true but required OIDC seed settings are missing; skipping OIDC seed."
        )
        return

    display_name = _normalize(settings.sso_oidc_display_name) or "MicroAge Sign-in"
    scopes = _normalize(settings.sso_oidc_scopes) or "openid email profile"
    group_claim = _normalize(settings.sso_oidc_group_claim) or "groups"

    providers = (
        await db.execute(
            select(IdentityProvider)
            .where(IdentityProvider.type == "oidc")
            .order_by(IdentityProvider.created_at.asc())
        )
    ).scalars().all()

    selected: IdentityProvider | None = None
    for p in providers:
        cfg = p.config_json or {}
        if _normalize(str(cfg.get("issuer", ""))).rstrip("/") == issuer.rstrip("/"):
            selected = p
            break
    if selected is None:
        for p in providers:
            if _normalize(p.name) == display_name:
                selected = p
                break

    desired_cfg = {
        "issuer": issuer,
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": scopes,
        "group_claim": group_claim,
    }

    if selected is None:
        db.add(
            IdentityProvider(
                name=display_name,
                type="oidc",
                enabled=True,
                button_label=display_name,
                config_json=_merge_idp_config({}, desired_cfg),
            )
        )
        await db.commit()
        log.info("Seeded central OIDC identity provider '%s'.", display_name)
        return

    selected.name = display_name
    selected.enabled = True
    selected.button_label = display_name
    selected.config_json = _merge_idp_config(selected.config_json or {}, desired_cfg)
    await db.commit()
    log.info("Updated central OIDC identity provider '%s'.", display_name)
