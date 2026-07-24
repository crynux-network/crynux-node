import yaml

from crynux_server.config import (
    Config,
    TaskErrorReportConfig,
    set_config,
    set_data_dir,
    set_task_error_report_automatic,
)


def test_task_error_report_defaults_false():
    assert TaskErrorReportConfig().automatic is False


def test_task_error_report_setting_is_written_atomically(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yml"
    config_file.write_text(
        "relay_url: test\n"
        "task_error_report:\n"
        "  automatic: false\n"
        "unrelated:\n"
        "  preserved: true\n",
        encoding="utf-8",
    )
    config = Config.model_construct(task_error_report=TaskErrorReportConfig())
    set_data_dir(str(tmp_path))
    set_config(config)
    try:
        set_task_error_report_automatic(True)
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert data["task_error_report"]["automatic"] is True
        assert data["unrelated"]["preserved"] is True
        assert not list(config_dir.glob("*.tmp"))
    finally:
        set_data_dir("")
