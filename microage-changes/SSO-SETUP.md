# MicroAge Central OIDC Setup

## Goal
Every deployed instance trusts one central MicroAge Entra ID OIDC app at startup (no per-instance `/admin/access` IdP entry).

## Required Entra app registration (MicroAge tenant)
- Create a **single-tenant** app registration in the MicroAge Entra directory.
- Register redirect URI:
  - `<public_base_url>/api/auth/oidc/{idp_id}/callback`
  - Backend callback route path is exactly: `/api/auth/oidc/{idp_id}/callback`.
- Create a client secret and store it in **Azure Key Vault**.
- API permissions/scopes used by the app: `openid email profile`.
- Optional: configure Entra group claim and set claim name to `groups` (or your configured claim key).

## Deployment env vars
- `SSO_OIDC_ENABLED` (`true`/`false`)
- `SSO_OIDC_DISPLAY_NAME` (default: `MicroAge Sign-in`)
- `SSO_OIDC_ISSUER` (example: `https://login.microsoftonline.com/<tenant-id>/v2.0`)
- `SSO_OIDC_CLIENT_ID`
- `SSO_OIDC_CLIENT_SECRET` (via Container App secret / `secretRef`)
- `SSO_OIDC_SCOPES` (default: `openid email profile`)
- `SSO_OIDC_GROUP_CLAIM` (default: `groups`)

## Notes
- `PUBLIC_BASE_URL` is already used by backend auth routes to build redirect URIs.
- OIDC client secrets are encrypted at rest by existing backend secret encryption (`app.core.crypto`) and are never returned in plaintext by admin APIs.
- Newly provisioned SSO users retain the safe default role (`noaccess`) unless explicitly mapped/granted.

## OPEN ITEM (marketplace scale)
Each instance has a unique Azure Container Apps URL, so redirect-URI strategy across many instances is still a design decision:
- wildcard redirect pattern feasibility, or
- per-instance custom domain strategy.

This PR intentionally does **not** solve that multi-instance redirect strategy.
