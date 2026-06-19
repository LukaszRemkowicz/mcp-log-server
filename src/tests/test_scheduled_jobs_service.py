from pathlib import Path

from services.scheduled_jobs_service import ScheduledJobsService


def test_scheduled_jobs_service_matches_cron_d_job(tmp_path: Path) -> None:
    cron_d = tmp_path / "etc" / "cron.d"
    cron_d.mkdir(parents=True)
    cron_file = cron_d / "agent-monitoring"
    cron_file.write_text(
        (
            "# ignored comment\n"
            "15 2 * * * root /opt/agent-monitoring/run-sitemap-analysis "
            ">> /var/log/devops/cron/agent-monitoring/sitemap-analysis.log 2>&1\n"
        ),
        encoding="utf-8",
    )

    inspection = ScheduledJobsService().inspect_project_scheduled_jobs(
        "landingpage",
        ["agent-monitoring", "sitemap-analysis"],
        roots=[cron_d],
    )

    assert inspection.truncated is False
    assert inspection.warnings == []
    assert len(inspection.matches) == 1
    match = inspection.matches[0]
    command_text = (
        "/opt/agent-monitoring/run-sitemap-analysis "
        ">> /var/log/devops/cron/agent-monitoring/sitemap-analysis.log 2>&1"
    )
    assert match.scheduler_type == "cron_d"
    assert match.path == cron_file.as_posix()
    assert match.line_number == 2
    assert match.schedule_context == "15 2 * * * root"
    assert match.command_text == command_text
    assert match.output_paths == [
        "/var/log/devops/cron/agent-monitoring/sitemap-analysis.log",
    ]
    assert match.matched_patterns == ["agent-monitoring", "sitemap-analysis"]


def test_scheduled_jobs_service_matches_systemd_unit(tmp_path: Path) -> None:
    systemd = tmp_path / "etc" / "systemd" / "system"
    systemd.mkdir(parents=True)
    unit_file = systemd / "agent-monitoring-sitemap.service"
    unit_file.write_text(
        (
            "[Unit]\n"
            "Description=Agent monitoring sitemap analysis\n"
            "[Service]\n"
            "ExecStart=/opt/agent-monitoring/sitemap-analysis --once\n"
        ),
        encoding="utf-8",
    )

    inspection = ScheduledJobsService().inspect_project_scheduled_jobs(
        "landingpage",
        ["sitemap-analysis"],
        roots=[systemd],
    )

    assert [match.scheduler_type for match in inspection.matches] == ["systemd"]
    assert inspection.matches[0].path == unit_file.as_posix()
    assert inspection.matches[0].line_number == 4
    assert inspection.matches[0].schedule_context == "Service.ExecStart"
    assert inspection.matches[0].command_text == "/opt/agent-monitoring/sitemap-analysis --once"


def test_scheduled_jobs_service_returns_no_match_result(tmp_path: Path) -> None:
    cron_d = tmp_path / "etc" / "cron.d"
    cron_d.mkdir(parents=True)
    (cron_d / "backup").write_text(
        "0 1 * * * root /usr/local/bin/backup\n",
        encoding="utf-8",
    )

    inspection = ScheduledJobsService().inspect_project_scheduled_jobs(
        "landingpage",
        ["sitemap-analysis"],
        roots=[cron_d],
    )

    assert inspection.matches == []
    assert inspection.warnings == []
    assert inspection.scheduler_roots == [cron_d.as_posix()]


def test_scheduled_jobs_service_warns_for_denied_root(tmp_path: Path) -> None:
    relative_root = Path("etc/cron.d")
    missing_root = tmp_path / "missing"

    inspection = ScheduledJobsService().inspect_project_scheduled_jobs(
        "landingpage",
        ["sitemap-analysis"],
        roots=[relative_root, missing_root],
    )

    assert inspection.matches == []
    assert [warning.warning_code for warning in inspection.warnings] == [
        "scheduler_root_not_absolute",
        "scheduler_root_missing",
    ]


def test_scheduled_jobs_service_does_not_return_unrelated_crontab_lines(tmp_path: Path) -> None:
    spool = tmp_path / "var" / "spool" / "cron"
    spool.mkdir(parents=True)
    (spool / "other-user").write_text(
        (
            "0 3 * * * /opt/unrelated/send-report\n"
            "30 3 * * * /opt/agent-monitoring/sitemap-analysis\n"
        ),
        encoding="utf-8",
    )

    inspection = ScheduledJobsService().inspect_project_scheduled_jobs(
        "landingpage",
        ["sitemap-analysis"],
        roots=[spool],
    )

    assert len(inspection.matches) == 1
    assert inspection.matches[0].line_number == 2
    assert "unrelated" not in inspection.matches[0].command_text
