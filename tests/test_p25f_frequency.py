"""P25 One-Frequency Trunk (P25F): the single frequency must round-trip and
upload as a site + trunk frequency."""
import os
import tempfile

from app.data.models import (
    ScannerConfig, System, Group, TalkGroup, TrunkFrequency, SYS_TYPE_P25_EDACS,
)
from app.data.file_996 import save, load

# Mirror of the character allow-list the upload worker builds internally.
SAFE = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -_&.'!"
)


def _make_p25f(freq="851.0125"):
    cfg = ScannerConfig()
    sysm = System(name="P25F Test", system_type=SYS_TYPE_P25_EDACS)
    sysm.group_id = "AABBCCDD00112233"
    if freq is not None:
        tf = TrunkFrequency()
        tf.frequency = freq
        tf.group_id = sysm.group_id
        sysm.trunk_frequencies.append(tf)
    grp = Group(name="TGs")
    grp.group_type = "2"
    grp.group_id = "1111222233334444"
    tg = TalkGroup()
    tg.name = "Disp"
    tg.tgid = "1001"
    tg.group_id = grp.group_id
    grp.channels.append(tg)
    sysm.groups.append(grp)
    cfg.systems.append(sysm)
    return cfg


def test_is_p25f_flag():
    cfg = _make_p25f()
    assert cfg.systems[0].is_p25f
    assert cfg.systems[0].is_p25


def test_p25f_frequency_round_trips_through_996():
    cfg = _make_p25f("851.0125")
    path = tempfile.mktemp(suffix=".996")
    try:
        save(cfg, path)
        reloaded = load(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    s = reloaded.systems[0]
    assert s.is_p25f
    assert [tf.frequency for tf in s.trunk_frequencies] == ["851.0125"]


def test_p25f_upload_creates_site_and_frequency():
    # Trace the protocol calls the upload worker issues for a P25F system.
    from PyQt6.QtWidgets import QApplication
    import app.ui.programmer.upload_dialog as ud

    QApplication.instance() or QApplication([])

    class FakeProto:
        def __init__(self):
            self.calls = []
            self._i = 0

        def _idx(self):
            self._i += 1
            return self._i

        def append_site(self, s, t):
            self.calls.append(("AST", t)); return self._idx()

        def set_site_info(self, *a):
            self.calls.append(("SIF",))

        def add_trunk_freq(self, s):
            self.calls.append(("ADD_TFQ",)); return self._idx()

        def set_trunk_freq(self, *a):
            self.calls.append(("TFQ", a[1]))  # a[1] = formatted freq

        def set_trunking_params(self, *a):
            self.calls.append(("TRN",))

        def append_tgid_group(self, s):
            self.calls.append(("AGT",)); return self._idx()

        def set_group_info(self, i, f):
            self.calls.append(("GIN",))

        def append_tgid(self, g):
            self.calls.append(("ACT",)); return self._idx()

        def set_tgid(self, *a):
            self.calls.append(("TIN",))

    cfg = _make_p25f("851.0125")
    worker = ud._UploadWorker(FakeProto(), cfg, [0], scanner_model="BCD996P2")
    worker.log_line.connect(lambda _s: None)
    worker._upload_p25_system(worker._proto, cfg.systems[0], "5", SAFE, 0, 10)

    calls = worker._proto.calls
    assert ("AST", "P25F") in calls, calls
    # 851.0125 MHz uploaded as an 8-digit zero-padded integer * 10000.
    assert ("TFQ", "08510125") in calls, calls
    # site precedes the frequency
    assert calls.index(("AST", "P25F")) < calls.index(("TFQ", "08510125"))


def test_p25f_upload_without_frequency_creates_no_site():
    from PyQt6.QtWidgets import QApplication
    import app.ui.programmer.upload_dialog as ud

    QApplication.instance() or QApplication([])

    class FakeProto:
        def __init__(self):
            self.calls = []
            self._i = 0

        def _idx(self):
            self._i += 1
            return self._i

        def append_site(self, s, t):
            self.calls.append(("AST", t)); return self._idx()

        def set_site_info(self, *a):
            self.calls.append(("SIF",))

        def set_trunking_params(self, *a):
            self.calls.append(("TRN",))

        def append_tgid_group(self, s):
            self.calls.append(("AGT",)); return self._idx()

        def set_group_info(self, i, f):
            self.calls.append(("GIN",))

        def append_tgid(self, g):
            self.calls.append(("ACT",)); return self._idx()

        def set_tgid(self, *a):
            self.calls.append(("TIN",))

    cfg = _make_p25f(freq=None)  # no frequency entered
    worker = ud._UploadWorker(FakeProto(), cfg, [0], scanner_model="BCD996P2")
    worker.log_line.connect(lambda _s: None)
    worker._upload_p25_system(worker._proto, cfg.systems[0], "5", SAFE, 0, 10)

    assert not any(c[0] == "AST" for c in worker._proto.calls)
