from bassierouter.cli import main
from bassierouter.config import Config, save_config


def _config_file(tmp_path):
    path = tmp_path / "config.json"
    save_config(Config(), path)
    return path


def test_config_option_after_subcommand(tmp_path, capsys):
    path = _config_file(tmp_path)
    assert main(["pending", "--config", str(path), "--root", str(tmp_path)]) == 0
    assert "Wijzigingen openstaan" in capsys.readouterr().out


def test_config_option_before_subcommand(tmp_path, capsys):
    path = _config_file(tmp_path)
    assert main(["--config", str(path), "--root", str(tmp_path), "pending"]) == 0
    assert "Wijzigingen openstaan" in capsys.readouterr().out


def test_mark_applied_with_option_after_subcommand(tmp_path, capsys):
    path = _config_file(tmp_path)
    assert main(["mark-applied", "--config", str(path), "--root", str(tmp_path)]) == 0
    assert (tmp_path / "applied-config.json").exists()


def test_simulate_nic_only_command(tmp_path, capsys):
    path = _config_file(tmp_path)
    assert main([
        "simulate", "--nic-only", "--config", str(path), "--root", str(tmp_path)
    ]) == 0
    assert "zonder PPPoE" in capsys.readouterr().out

