"""Tests for the Bulgarian tracker's TLS context."""

import ssl

from custom_components.express_one.tls import SECTIGO_DV_R36_PEM, build_ssl_context


def test_the_intermediate_certificate_is_a_pem():
    assert SECTIGO_DV_R36_PEM.startswith("-----BEGIN CERTIFICATE-----")
    assert SECTIGO_DV_R36_PEM.rstrip().endswith("-----END CERTIFICATE-----")


def test_context_loads_and_keeps_verification_on():
    context = build_ssl_context()

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert any(
        "Sectigo Public Server Authentication CA DV R36" in str(cert)
        for cert in context.get_ca_certs()
    )
