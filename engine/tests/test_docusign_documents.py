"""Token-free executed-contract section-location tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.docusign_documents import (  # noqa: E402
    reasoning_excerpt, sections_from_page_text,
)


def test_locator_skips_toc_fees_and_stops_at_next_heading():
    pages = [
        "ACME MSA + SOW\nFees ........ 7\nThis SOW is effective September 1, 2026.",
        "1. Services\nImplement revenue operations.\n2. Confidentiality\nKeep data private.",
        "4. Fees\nFixed fee: $25,000\nInvoices: 50% at signature.\n5. Intellectual Property\nWork product terms.",
        "8. Term and Termination\nThe term ends December 31, 2026.\n9. General",
    ]
    sections = sections_from_page_text(pages)
    assert "$25,000" in sections["fees"].text
    assert "Intellectual Property" not in sections["fees"].text
    assert sections["fees"].pages == (3,)
    excerpt = reasoning_excerpt(sections)
    assert "=== fees ===" in excerpt
    assert "Confidentiality" not in excerpt


def test_locator_requires_an_unambiguous_fees_heading():
    try:
        sections_from_page_text([
            "Agreement\nThe customer will pay money under terms discussed elsewhere."])
    except ValueError as error:
        assert "Fees section" in str(error)
    else:
        raise AssertionError("missing Fees heading must route to review")


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test(); print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
