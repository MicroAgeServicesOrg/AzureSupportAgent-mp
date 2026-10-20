from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def deployment():
    template = json.loads(
        (Path(__file__).resolve().parents[2] / "deploy" / "main.json").read_text()
    )
    app = next(
        resource for resource in template["resources"]
        if resource["type"] == "Microsoft.App/containerApps"
    )
    return template, app["properties"]


def test_oidc_is_opt_in_and_client_secret_is_secure(deployment):
    template, _ = deployment
    assert template["parameters"]["ssoOidcEnabled"]["defaultValue"] is False
    assert template["parameters"]["ssoOidcClientSecret"]["type"].lower() == "securestring"
    assert template["parameters"]["ssoOidcClientSecret"]["defaultValue"] == ""


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SSO_OIDC_ENABLED", "string(parameters('ssoOidcEnabled'))"),
        ("SSO_OIDC_DISPLAY_NAME", "parameters('ssoOidcDisplayName')"),
        ("SSO_OIDC_ISSUER", "parameters('ssoOidcIssuer')"),
        ("SSO_OIDC_CLIENT_ID", "parameters('ssoOidcClientId')"),
        ("SSO_OIDC_SCOPES", "parameters('ssoOidcScopes')"),
        ("SSO_OIDC_GROUP_CLAIM", "parameters('ssoOidcGroupClaim')"),
    ],
)
def test_oidc_parameters_reach_container(deployment, name, value):
    _, app = deployment
    env = app["template"]["containers"][0]["env"]
    assert f"createObject('name', '{name}', 'value', {value})" in env


def test_oidc_secret_composes_with_upstream_secrets(deployment):
    template, app = deployment
    secrets = app["configuration"]["secrets"]
    assert secrets.startswith("[concat(")
    assert (
        "createObject('name', 'sso-oidc-client-secret', "
        "'value', parameters('ssoOidcClientSecret'))"
    ) in secrets
    assert "parameters('databaseUrlSecretUri')" in secrets
    assert "variables('adminPasswordSecret')" in secrets
    assert "variables('secretsEncryptionKeySecret')" in secrets
    assert "keyVaultUrl" in template["variables"]["adminPasswordSecret"]
    assert "keyVaultUrl" in template["variables"]["secretsEncryptionKeySecret"]
    env = app["template"]["containers"][0]["env"]
    assert (
        "createObject('name', 'SSO_OIDC_CLIENT_SECRET', "
        "'secretRef', 'sso-oidc-client-secret')"
    ) in env
    assert "'value', parameters('ssoOidcClientSecret')" not in env
