from types import SimpleNamespace

from backend.services import system_info


def test_io_throughput_uses_live_counter_deltas(monkeypatch):
    disk_samples = iter(
        [
            SimpleNamespace(read_bytes=1000, write_bytes=2000),
            SimpleNamespace(read_bytes=1300, write_bytes=2300),
        ]
    )
    network_samples = iter(
        [
            {"Wi-Fi": SimpleNamespace(bytes_recv=10000, bytes_sent=20000)},
            {"Wi-Fi": SimpleNamespace(bytes_recv=10600, bytes_sent=20300)},
        ]
    )
    times = iter([10.0, 13.0])
    monkeypatch.setattr(system_info, "_last_io_sample", None)
    monkeypatch.setattr(system_info, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        system_info.psutil,
        "disk_io_counters",
        lambda: next(disk_samples),
    )
    monkeypatch.setattr(
        system_info.psutil,
        "net_io_counters",
        lambda pernic=False: next(network_samples)
        if pernic
        else SimpleNamespace(bytes_recv=0, bytes_sent=0),
    )
    monkeypatch.setattr(
        system_info.psutil,
        "net_if_stats",
        lambda: {"Wi-Fi": SimpleNamespace(isup=True)},
    )
    inventory = {"network": [{"Name": "Wi-Fi", "Status": "Up"}]}

    assert system_info._live_io_rates(inventory) == (None, None, None, None)
    assert system_info._live_io_rates(inventory) == (100.0, 100.0, 200.0, 100.0)
