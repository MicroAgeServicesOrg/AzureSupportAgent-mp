# MicroAge ASA — Fork Change Log

Tracks every customization on top of upstream (zmustafa/AzureSupportAgent)
so upstream merges stay predictable.

## Principle
Keep changes at the EDGES (config, env, additive files). Avoid editing
upstream core logic. Everything below is a merge-conflict risk — keep it short.

## Config / env changes (zero conflict risk)
- Added env-driven central OIDC IdP startup seed for fleet-wide sign-in with:
  `SSO_OIDC_ENABLED`, `SSO_OIDC_DISPLAY_NAME`, `SSO_OIDC_ISSUER`,
  `SSO_OIDC_CLIENT_ID`, `SSO_OIDC_CLIENT_SECRET`, `SSO_OIDC_SCOPES`,
  `SSO_OIDC_GROUP_CLAIM`.
- Added deploy/main.bicep parameters and env wiring for the same `SSO_OIDC_*` values.
- `SSO_OIDC_CLIENT_SECRET` is delivered via Container App secret + `secretRef` only.

## Additive files (low risk)
- `backend/app/auth/oidc_seed.py` (startup seed module for central OIDC IdP)
- `microage-changes/SSO-SETUP.md` (MicroAge central OIDC setup/runbook)

## Core edits (HIGH risk — document exactly what & why)
- `backend/app/main.py`: additive startup hook calling `seed_oidc_provider` after `seed_admin`.

## Fixes contributed back upstream (PR link)
- (none yet)