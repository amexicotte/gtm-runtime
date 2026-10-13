"""Static release wiring checks that need no production credentials."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_business_listening_workflows_share_one_mutation_lock():
    expected = "group: harbor-business-listening-writes"
    for name in ("email-listening-nightly.yml", "fireflies-monday-poller.yml",
                 "portfolio-pattern-weekly.yml", "activity-alerts-daily.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text()
        assert expected in workflow
        assert "cancel-in-progress: false" in workflow


def test_due_activity_alerts_have_redundant_daily_production_schedule():
    workflow = (ROOT / ".github" / "workflows" /
                "activity-alerts-daily.yml").read_text()
    assert 'cron: "5 12,14 * * *"' in workflow
    assert "runtime.activity_alert_main" in workflow
    assert "MONDAY_API_TOKEN: ${{ secrets.MONDAY_API_KEY }}" in workflow
    assert "SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}" in workflow
    assert "BUSINESS_LISTENING_WRITE_KILL_SWITCH" in workflow
    assert "FIREFLIES_API_KEY" not in workflow
    assert "OPENROUTER_API_KEY" not in workflow


def test_nightly_email_schedule_uses_explicit_fail_closed_rollout_variables():
    workflow = (ROOT / ".github" / "workflows" /
                "email-listening-nightly.yml").read_text()
    assert "EMAIL_NIGHTLY_ENABLED: ${{ vars.EMAIL_NIGHTLY_ENABLED }}" in workflow
    assert '"$EMAIL_NIGHTLY_ENABLED" != "true"' in workflow
    assert "EMAIL_RUN_MODE: ${{ vars.EMAIL_RUN_MODE }}" in workflow
    assert "EMAIL_REASONING_APPROVED: ${{ vars.EMAIL_REASONING_APPROVED }}" in workflow
    assert "EMAIL_AUTOMATIC_WRITES_APPROVED: ${{ vars.EMAIL_AUTOMATIC_WRITES_APPROVED }}" in workflow
    assert "EMAIL_LISTENING_ACTIVATED_AT: ${{ vars.EMAIL_LISTENING_ACTIVATED_AT }}" in workflow
    assert "MONDAY_AGENT_EVENTS_BOARD_ID: ${{ vars.MONDAY_AGENT_EVENTS_BOARD_ID }}" in workflow
    assert "BUSINESS_CONTEXT_ENABLED: ${{ vars.BUSINESS_CONTEXT_ENABLED }}" in workflow
    assert "NOTION_API_TOKEN: ${{ secrets.NOTION_API_TOKEN }}" in workflow
    assert "NOTION_AGENT_ROOT_PAGE_ID" not in workflow
    assert "RCLONE_CONFIG_HARBOR-DRIVE_SCOPE: drive.readonly" in workflow
    assert "RCLONE_CONFIG_HARBOR-DRIVE_TOKEN: ${{ secrets.GOOGLE_DRIVE_RCLONE_TOKEN }}" in workflow
    assert "Install read-only Drive CLI" in workflow
    assert "scripts/install_pinned_rclone.sh" in workflow
    assert "EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID: ${{ vars.EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID }}" in workflow
    assert "EMAIL_CONTACT_IDENTITY_RESOLUTIONS: ${{ vars.EMAIL_CONTACT_IDENTITY_RESOLUTIONS }}" in workflow
    assert "|| 'true'" not in workflow
    assert "verification_contact_ids" in workflow
    assert "manual live verification requires" in workflow
    assert "SINCE_INPUT: ${{ inputs.since }}" in workflow
    assert '--since "$SINCE_INPUT"' in workflow
    assert '--since "${{ inputs.since }}"' not in workflow
    assert "python -m runtime.email_shadow_review" in workflow
    assert "email-shadow-review.json" in workflow
    assert '- cron: "0 6 * * *"' in workflow
    assert '- cron: "0 7 * * *"' in workflow
    assert 'EVENT_SCHEDULE: ${{ github.event.schedule }}' in workflow
    assert '[[ "$EVENT_SCHEDULE" == "$EXPECTED_SCHEDULE" ]]' in workflow
    assert '[[ "$(TZ=America/New_York date +%H)" != "02" ]]' not in workflow
    assert "schedule-gate.json" in workflow
    assert "Restore schedule gate after checkout cleanup" in workflow
    assert "schedule_eligible=$SCHEDULE_ELIGIBLE" in workflow
    assert '--scheduled-for "${{ steps.time_gate.outputs.scheduled_for }}"' in workflow


def test_docusign_runs_sequentially_after_email_with_separate_reasoning_and_drive_gates():
    workflow = (ROOT / ".github" / "workflows" /
                "email-listening-nightly.yml").read_text()
    assert workflow.index("Run email reconciliation") < workflow.index(
        "Run bounded executed-contract reconciliation after email")
    assert "vars.DOCUSIGN_NIGHTLY_ENABLED == 'true'" in workflow
    assert "DOCUSIGN_REASONING_APPROVED: ${{ vars.DOCUSIGN_REASONING_APPROVED }}" in workflow
    assert "DOCUSIGN_AUTOMATIC_WRITES_APPROVED: ${{ vars.DOCUSIGN_AUTOMATIC_WRITES_APPROVED }}" in workflow
    assert "DOCUSIGN_DOCUMENT_ARCHIVE_APPROVED: ${{ vars.DOCUSIGN_DOCUMENT_ARCHIVE_APPROVED }}" in workflow
    assert "RCLONE_CONFIG_HARBOR-DRIVE-WRITER_SCOPE: drive" in workflow
    assert "GOOGLE_DRIVE_WRITER_RCLONE_TOKEN" in workflow
    assert "runtime.docusign_main run" in workflow
    assert "docusign-contract-report.json" in workflow


def test_weekly_business_context_refresh_is_readonly_and_fail_closed():
    workflow = (ROOT / ".github" / "workflows" /
                "business-context-weekly.yml").read_text()
    assert "runtime.context_main website-refresh" in workflow
    assert "vars.BUSINESS_CONTEXT_ENABLED == 'true'" in workflow
    assert "RCLONE_CONFIG_HARBOR-DRIVE_SCOPE: drive.readonly" in workflow
    assert "GOOGLE_DRIVE_ROOT_FOLDER_ID" not in workflow
    assert "runtime.context_main audit" in workflow
    assert "inputs.audit_private_context" in workflow
    assert "notion-root-discovery" not in workflow
    assert "notion-seed" not in workflow
    assert "scripts/install_pinned_rclone.sh" in workflow


def test_rclone_installer_is_official_version_pinned_and_checksum_verified():
    installer = (ROOT / "scripts" / "install_pinned_rclone.sh").read_text()
    assert "downloads.rclone.org/v${RCLONE_VERSION}" in installer
    assert 'RCLONE_VERSION="1.75.0"' in installer
    assert "aa2804e08f48250e71009c727124b6341cd1000000016a9a09d14663cabafbaa" in installer
    assert "sha256sum --check --status" in installer


def test_portfolio_pattern_workflow_is_advisory_and_approval_gated():
    workflow = (ROOT / ".github" / "workflows" /
                "portfolio-pattern-weekly.yml").read_text()
    assert "vars.PORTFOLIO_PATTERN_ENABLED == 'true'" in workflow
    assert "PORTFOLIO_REASONING_APPROVED: ${{ vars.PORTFOLIO_REASONING_APPROVED }}" in workflow
    assert "PORTFOLIO_PATTERN_WRITES_APPROVED: ${{ vars.PORTFOLIO_PATTERN_WRITES_APPROVED }}" in workflow
    assert "runtime.portfolio_main" in workflow
    assert "EMAIL_RUN_MODE" not in workflow


def test_phase2_rollout_supervisor_is_cloud_deterministic_and_self_retires():
    workflow = (ROOT / ".github" / "workflows" /
                "phase2-rollout-supervisor.yml").read_text()
    assert "reconcile_phase2_shadow_observations.py" in workflow
    assert "prepare_phase2_reversible_records.py --reset" in workflow
    assert "verify_phase2_live_state.py preflight" in workflow
    assert "--verification-contact-ids 10000000011" in workflow
    assert "verify_phase2_live_state.py first" in workflow
    assert "verify_phase2_live_state.py retry" in workflow
    assert "'.threads_reasoned == 0'" in workflow
    assert "prepare_phase2_verification_timeline.py --cleanup" in workflow
    assert "Always clean the synthetic timeline fixture" in workflow
    assert "if: ${{ always() && steps.collect.outputs.ready == 'true' }}" in workflow
    assert "scripts/finalize_phase2_rollout.py" in workflow
    assert "docs/phase-2-rollout-activation.json" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "AUTONOMOUS_ROLLOUT_GITHUB_TOKEN" not in workflow
    assert "contents: write" in workflow
    assert "OPENROUTER_MODEL: ${{ vars.OPENROUTER_MODEL }}" in workflow
    email = (ROOT / ".github" / "workflows" /
             "email-listening-nightly.yml").read_text()
    assert "Read committed rollout activation" in email
    assert '"$ROLLOUT_FILE_LIVE" == "true"' in email
    assert 'export EMAIL_AUTOMATIC_WRITES_APPROVED=true' in email
    assert 'export EMAIL_IMMEDIATE_ROLLOUT_APPROVED=true' in email
    assert "ROLLOUT_FILE_VALIDATION_MODE" in email
    assert "immediate_validation" in workflow
    assert "prepare_phase2_verification_timeline.py --create" in workflow
    assert "--immediate-validation" in workflow


def test_github_actions_usage_monitor_is_readonly_and_slack_bounded():
    workflow = (ROOT / ".github" / "workflows" /
                "github-actions-usage-monitor.yml").read_text()
    assert "actions: read" in workflow
    assert "contents: read" in workflow
    assert "GITHUB_REPOSITORY_TOKEN: ${{ github.token }}" in workflow
    assert "GITHUB_BILLING_READ_TOKEN: ${{ secrets.GITHUB_BILLING_READ_TOKEN }}" in workflow
    assert "ACTIONS_MONTHLY_MINUTES_LIMIT" in workflow
    assert 'ACTIONS_USAGE_ALERT_THRESHOLDS: "75,90,100"' in workflow
    assert "SLACK_USAGE_ALERT_USER_ID" in workflow
    assert "SLACK_USAGE_ALERT_CHANNEL" in workflow
    assert "runtime.github_actions_usage_main" in workflow
    assert "cache-dependency-path: engine/requirements.txt" in workflow
    assert "steps.usage.outputs.marker_created == 'true'" in workflow
    assert "retention-days: 90" in workflow


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
