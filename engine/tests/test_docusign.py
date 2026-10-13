"""DocuSign JWT, recipient metadata, and executed-PDF tests."""
import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.docusign import DocuSign, consent_url  # noqa: E402


def response(payload=None, *, content=b""):
    value = Mock(ok=True, status_code=200, content=content)
    value.json.return_value = payload or {}
    return value


def test_jwt_connectivity_resolves_configured_account():
    session = Mock()
    session.post.return_value = response({"access_token": "token"})
    session.get.return_value = response({"accounts": [{
        "account_id": "acct", "base_uri": "https://na4.docusign.net"}]})
    client = DocuSign(session=session)
    with patch("runtime.docusign.config.DOCUSIGN_INTEGRATION_KEY", "ikey"), \
            patch("runtime.docusign.config.DOCUSIGN_USER_ID", "user"), \
            patch("runtime.docusign.config.DOCUSIGN_ACCOUNT_ID", "acct"), \
            patch("runtime.docusign.config.DOCUSIGN_PRIVATE_KEY", "private"), \
            patch("runtime.docusign.jwt.encode", return_value="assertion"):
        result = client.connectivity()
    assert result["status"] == "ready"
    assert result["account_id"] == "acct"


def test_completed_envelope_exposes_signer_and_copy_recipient_emails():
    client = DocuSign(session=Mock())
    client._access_token = "token"
    client._base_uri = "https://na4.docusign.net"
    client.session.get.side_effect = [
        response({"envelopeId": "env", "status": "completed",
                  "completedDateTime": "2026-08-23T17:30:00Z"}),
        response({
            "signers": [{"name": "Jane", "email": "jane@buyer.example",
                         "status": "completed"}],
            "carbonCopies": [{"name": "Legal", "email": "legal@buyer.example"}],
        }),
    ]
    with patch("runtime.docusign.config.DOCUSIGN_ACCOUNT_ID", "acct"):
        value = client.completed_envelope("env")
    assert value.signer_emails == ("jane@buyer.example",)
    assert value.recipient_emails == (
        "jane@buyer.example", "legal@buyer.example")


def test_download_combined_pdf_reads_signed_agreement_with_certificate():
    client = DocuSign(session=Mock())
    client._access_token = "token"
    client._base_uri = "https://na4.docusign.net"
    client.session.get.return_value = response(content=b"%PDF-signed")
    with patch("runtime.docusign.config.DOCUSIGN_ACCOUNT_ID", "acct"):
        assert client.download_combined_pdf("env") == b"%PDF-signed"
    _, kwargs = client.session.get.call_args
    assert kwargs["params"] == {"certificate": "true"}


def test_consent_url_accepts_a_full_auth_server_url():
    value = consent_url(
        "ikey", "https://example.test/callback",
        auth_server="https://account-d.docusign.com/")
    assert value.startswith("https://account-d.docusign.com/oauth/auth?")
    assert "scope=signature+impersonation" in value


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test(); print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
