"""CLI for DocuSign connectivity, schema preflight, and reconciliation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .email_main import _load_env_file


def _write(payload: dict, path: str = "") -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path:
        Path(path).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("connectivity")
    sub.add_parser("archive-connectivity")
    consent = sub.add_parser("consent-url")
    consent.add_argument("--integration-key", required=True)
    consent.add_argument("--redirect-uri", required=True)
    consent.add_argument("--auth-server", default="account.docusign.com")
    schema = sub.add_parser("schema")
    schema.add_argument("--apply", action="store_true")
    schema.add_argument(
        "--update-deal-fee-formula", action="store_true",
        help="explicitly update a drifted Calculated Deal Value formula in place",
    )
    run = sub.add_parser("run")
    run.add_argument("--mode", choices=("shadow", "live"), default="shadow")
    run.add_argument("--since", default="")
    run.add_argument("--output", default="")
    args = parser.parse_args()
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")

    try:
        if args.command == "consent-url":
            from .docusign import consent_url
            _write({"consent_url": consent_url(
                args.integration_key, args.redirect_uri,
                auth_server=args.auth_server)})
            return 0
        if args.command == "connectivity":
            from .config import config
            from .docusign import DocuSign
            config.require_docusign_read()
            _write(DocuSign().connectivity())
            return 0
        if args.command == "archive-connectivity":
            from .drive_archive import SignedContractArchive
            _write(SignedContractArchive().connectivity())
            return 0
        if args.command == "schema":
            from .monday import Monday
            crm = Monday()
            if args.apply:
                _write(crm.prepare_docusign_schema(
                    update_deal_fee_formula=args.update_deal_fee_formula))
            else:
                crm.verify_docusign_schema()
                _write({"status": "ready"})
            return 0
        from .docusign_runner import run_docusign
        stats = run_docusign(
            shadow=args.mode == "shadow", since=args.since)
        _write(stats.to_dict(), args.output)
        return 0 if not stats.envelopes_failed else 2
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"DocuSign error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
