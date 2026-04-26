from __future__ import annotations

from unittest.mock import patch

from utils.container_inspection_commands import (
    ContainerPathStat,
    list_container_directory,
    read_container_file,
    stat_container_path,
)


class FakeExecResult:
    def __init__(self, *, exit_code: int = 0, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output.encode("utf-8")


class FakeContainer:
    def __init__(self, outputs_by_command: dict[tuple[str, ...], str]) -> None:
        self.outputs_by_command = outputs_by_command
        self.commands: list[list[str]] = []

    def exec_run(
        self,
        command: list[str],
        stdout: bool = True,
        stderr: bool = True,
    ) -> FakeExecResult:
        assert stdout is True
        assert stderr is True
        self.commands.append(command)
        command_key = tuple(command)
        return FakeExecResult(output=self.outputs_by_command[command_key])


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container

    def get(self, container_name: str) -> FakeContainer:
        assert container_name == "backend-container"
        return self.container


class FakeDockerClient:
    def __init__(self, container: FakeContainer) -> None:
        self.containers = FakeContainers(container)


def test_stat_container_path_runs_only_the_approved_find_and_stat_command() -> None:
    expected_command = [
        "find",
        "/app/manage.py",
        "-maxdepth",
        "0",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    container = FakeContainer(
        {
            tuple(expected_command): "regular file\t661\t755\t1775110909\t/app/manage.py\n",
        }
    )

    with patch(
        "utils.container_inspection_commands.docker.from_env",
        return_value=FakeDockerClient(container),
    ):
        result = stat_container_path("backend-container", "/app/manage.py")

    assert result == ContainerPathStat(
        path="/app/manage.py",
        is_dir=False,
        size=661,
        mode=0o755,
        modified_at="2026-04-02T06:21:49+00:00",
    )
    assert container.commands == [expected_command]


def test_read_container_file_runs_only_approved_stat_then_cat_commands() -> None:
    stat_command = [
        "find",
        "/app/manage.py",
        "-maxdepth",
        "0",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    cat_command = [
        "find",
        "/app/manage.py",
        "-maxdepth",
        "0",
        "-type",
        "f",
        "-exec",
        "cat",
        "{}",
        ";",
    ]
    container = FakeContainer(
        {
            tuple(stat_command): "regular file\t661\t755\t1775110909\t/app/manage.py\n",
            tuple(cat_command): "#!/usr/bin/env python\n",
        }
    )

    with patch(
        "utils.container_inspection_commands.docker.from_env",
        return_value=FakeDockerClient(container),
    ):
        content, truncated = read_container_file("backend-container", "/app/manage.py")

    assert content == "#!/usr/bin/env python\n"
    assert truncated is False
    assert container.commands == [stat_command, cat_command]


def test_list_container_directory_runs_only_approved_stat_commands() -> None:
    directory_stat_command = [
        "find",
        "/app/settings",
        "-maxdepth",
        "0",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    list_command = [
        "find",
        "/app/settings",
        "-mindepth",
        "1",
        "-maxdepth",
        "1",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    container = FakeContainer(
        {
            tuple(directory_stat_command): "directory\t224\t755\t1777168800\t/app/settings\n",
            tuple(list_command): (
                "regular file\t1024\t644\t1777168800\t/app/settings/base.py\n"
                "directory\t160\t755\t1777168800\t/app/settings/dev\n"
            ),
        }
    )

    with patch(
        "utils.container_inspection_commands.docker.from_env",
        return_value=FakeDockerClient(container),
    ):
        entries, truncated = list_container_directory("backend-container", "/app/settings")

    assert truncated is False
    assert entries == [
        ContainerPathStat(
            path="/app/settings/dev",
            is_dir=True,
            size=160,
            mode=0o755,
            modified_at="2026-04-26T02:00:00+00:00",
        ),
        ContainerPathStat(
            path="/app/settings/base.py",
            is_dir=False,
            size=1024,
            mode=0o644,
            modified_at="2026-04-26T02:00:00+00:00",
        ),
    ]
    assert container.commands == [directory_stat_command, list_command]
