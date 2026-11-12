import tomllib
from pathlib import Path
import pytest
from jobs import SourceFolderConfig


def test_windows_paths_in_source_folder_config():
    """Verify that Windows paths with drive letters and backslashes are parsed correctly."""
    # Standard Windows path with backslashes
    src1 = SourceFolderConfig(path=r"E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM_HRM")
    assert src1.name == "L60SANGTAM_HRM"

    # Windows path with trailing backslash
    src2 = SourceFolderConfig(path=r"C:\Database\Backups\\")
    assert src2.name == "Backups"

    # Mixed forward and backward slashes
    src3 = SourceFolderConfig(path="D:/Storage/SQL_2017\\DB_Archive")
    assert src3.name == "DB_Archive"


@pytest.mark.parametrize("config_path", ["mise.toml", "mise.production.toml"])
def test_mise_daemons_windows_compatibility(config_path):
    """Verify daemon configuration is 100% compatible with Windows cmd /C shell."""
    file_path = Path(config_path)
    assert file_path.exists(), f"{config_path} must exist"

    with open(file_path, "rb") as f:
        data = tomllib.load(f)

    assert "daemons" in data, f"{config_path} must define [daemons]"
    daemons = data["daemons"]

    for name, daemon_cfg in daemons.items():
        run_cmd = daemon_cfg.get("run", "")
        # 'exec' is a POSIX built-in that fails on Windows cmd /C
        assert not run_cmd.strip().startswith("exec "), (
            f"Daemon '{name}' in {config_path} must not use 'exec' (incompatible with Windows cmd /C): {run_cmd}"
        )

        # 'ready_cmd' with curl is fragile or non-existent on Windows
        assert "ready_cmd" not in daemon_cfg, (
            f"Daemon '{name}' in {config_path} should not use shell 'ready_cmd'; use cross-platform 'ready_port' instead"
        )

        # Must use ready_port for cross-platform TCP checking
        assert "ready_port" in daemon_cfg, (
            f"Daemon '{name}' in {config_path} must define 'ready_port' for socket readiness"
        )
