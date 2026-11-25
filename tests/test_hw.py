from bassierouter.hw import cpu_info, temperatures


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_temperatures_reads_coretemp_labels(tmp_path):
    _write(tmp_path / "sys/class/hwmon/hwmon0/temp1_input", "59000\n")
    _write(tmp_path / "sys/class/hwmon/hwmon0/temp1_label", "Core 0\n")
    _write(tmp_path / "sys/class/hwmon/hwmon0/temp2_input", "56000\n")
    _write(tmp_path / "sys/class/hwmon/hwmon0/temp2_label", "Core 1\n")
    assert temperatures(tmp_path) == [
        {"label": "Core 0", "celsius": 59.0},
        {"label": "Core 1", "celsius": 56.0},
    ]


def test_temperatures_falls_back_to_thermal_zone(tmp_path):
    _write(tmp_path / "sys/class/thermal/thermal_zone0/temp", "26800\n")
    _write(tmp_path / "sys/class/thermal/thermal_zone0/type", "TZ01\n")
    assert temperatures(tmp_path) == [{"label": "TZ01", "celsius": 26.8}]


def test_cpu_info_reports_settable_range(tmp_path):
    base = tmp_path / "sys/devices/system/cpu/cpu0/cpufreq"
    _write(base / "scaling_governor", "powersave")
    _write(base / "scaling_min_freq", "800000")
    _write(base / "scaling_max_freq", "1600000")
    _write(base / "scaling_available_frequencies", "2500000 480000 800000 1600000")
    _write(base / "scaling_available_governors", "powersave performance")
    _write(
        tmp_path / "proc/cpuinfo",
        "processor\t: 0\nmodel name\t: Intel(R) Pentium(R) CPU N3710 @ 1.60GHz\n",
    )
    info = cpu_info(tmp_path)
    assert info["available"]
    assert info["governor"] == "powersave"
    assert info["min_mhz"] == 800
    assert info["max_mhz"] == 1600
    assert info["available_mhz"] == [480, 800, 1600, 2500]
    assert info["available_governors"] == ["powersave", "performance"]
    assert info["nominal_mhz"] == 1600
    assert info["suggested_min_mhz"] == 800
    assert info["suggested_max_mhz"] == 1600


def test_cpu_info_without_sysfs(tmp_path):
    info = cpu_info(tmp_path)
    assert info["available"] is False
    assert info["available_mhz"] == []
