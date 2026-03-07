"""
Tests for the .996 file parser and writer (app/data/file_996.py).

Covers:
- Load correctness for a conventional file (sample.996)
- Load correctness for a mixed conventional+Motorola file (march-lineup.996)
- Round-trip fidelity: load → save → reload preserves system/group/channel counts
  and key field values for both file types
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.data import file_996
from app.data.models import Channel, ScannerConfig, TalkGroup, TrunkFrequency

SAMPLE_996 = Path(__file__).parent.parent / "sample-data" / "sample.996"
MARCH_996  = Path(__file__).parent.parent / "sample-data" / "march-lineup.996"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _total_channels(config: ScannerConfig) -> int:
    return sum(
        len(g.channels)
        for s in config.systems
        for g in s.groups
    )

def _total_trunk_freqs(config: ScannerConfig) -> int:
    return sum(len(s.trunk_frequencies) for s in config.systems)

def _total_tgids(config: ScannerConfig) -> int:
    return sum(
        len(g.channels)
        for s in config.systems
        for g in s.groups
        if any(isinstance(c, TalkGroup) for c in g.channels)
    )

def _roundtrip(config: ScannerConfig) -> ScannerConfig:
    """Save config to a temp file and reload it."""
    with tempfile.NamedTemporaryFile(suffix=".996", delete=False) as f:
        tmp = Path(f.name)
    file_996.save(config, tmp)
    return file_996.load(tmp)


# ---------------------------------------------------------------------------
# sample.996 — conventional channels only
# ---------------------------------------------------------------------------

class TestSample996Load:
    @pytest.fixture(scope="class")
    def config(self):
        return file_996.load(SAMPLE_996)

    def test_loads_without_error(self, config):
        assert config is not None

    def test_system_count(self, config):
        assert len(config.systems) > 0

    def test_channel_count(self, config):
        # sample.996 is known to have 359 channels
        assert _total_channels(config) == 359

    def test_system_group_ids_present(self, config):
        # Systems that have names should also have group IDs
        for sys in config.systems:
            if sys.name:
                assert sys.group_id, f"Named system {sys.name!r} has no group_id"

    def test_system_group_ids_unique(self, config):
        ids = [s.group_id for s in config.systems if s.group_id]
        assert len(ids) == len(set(ids)), "Duplicate system group_ids"

    def test_channel_frequencies_valid(self, config):
        for sys in config.systems:
            for grp in sys.groups:
                for ch in grp.channels:
                    if isinstance(ch, Channel):
                        freq = float(ch.frequency)
                        assert freq > 0, f"Channel {ch.name!r} has non-positive frequency"


class TestSample996RoundTrip:
    @pytest.fixture(scope="class")
    def original(self):
        return file_996.load(SAMPLE_996)

    @pytest.fixture(scope="class")
    def reloaded(self, original):
        return _roundtrip(original)

    def test_system_count_preserved(self, original, reloaded):
        assert len(reloaded.systems) == len(original.systems)

    def test_channel_count_preserved(self, original, reloaded):
        assert _total_channels(reloaded) == _total_channels(original)

    def test_system_names_preserved(self, original, reloaded):
        for orig_sys, rel_sys in zip(original.systems, reloaded.systems):
            assert rel_sys.name == orig_sys.name

    def test_system_types_preserved(self, original, reloaded):
        for orig_sys, rel_sys in zip(original.systems, reloaded.systems):
            assert rel_sys.system_type == orig_sys.system_type

    def test_channel_frequencies_preserved(self, original, reloaded):
        orig_freqs = sorted(
            ch.frequency
            for s in original.systems
            for g in s.groups
            for ch in g.channels
            if isinstance(ch, Channel)
        )
        rel_freqs = sorted(
            ch.frequency
            for s in reloaded.systems
            for g in s.groups
            for ch in g.channels
            if isinstance(ch, Channel)
        )
        assert rel_freqs == orig_freqs

    def test_group_counts_preserved(self, original, reloaded):
        for orig_sys, rel_sys in zip(original.systems, reloaded.systems):
            assert len(rel_sys.groups) == len(orig_sys.groups)

    def test_file_header_written(self, original):
        with tempfile.NamedTemporaryFile(suffix=".996", delete=False) as f:
            tmp = Path(f.name)
        file_996.save(original, tmp)
        first_line = tmp.read_text(encoding="latin-1").splitlines()[0]
        assert first_line == '".7BCD996T"'


# ---------------------------------------------------------------------------
# march-lineup.996 — conventional + Motorola trunked
# ---------------------------------------------------------------------------

class TestMarch996Load:
    @pytest.fixture(scope="class")
    def config(self):
        return file_996.load(MARCH_996)

    def test_loads_without_error(self, config):
        assert config is not None

    def test_system_count(self, config):
        assert len(config.systems) == 2

    def test_has_conventional_system(self, config):
        conv = [s for s in config.systems if s.is_conventional]
        assert len(conv) == 1
        assert conv[0].name == "Public Safety"

    def test_has_motorola_system(self, config):
        mot = [s for s in config.systems if s.is_motorola]
        assert len(mot) == 1
        assert mot[0].name == "MA State Police"

    def test_trunk_frequencies_loaded(self, config):
        mot = next(s for s in config.systems if s.is_motorola)
        assert len(mot.trunk_frequencies) == 9

    def test_trunk_frequencies_have_valid_freq(self, config):
        mot = next(s for s in config.systems if s.is_motorola)
        for tf in mot.trunk_frequencies:
            freq = float(tf.frequency)
            assert freq > 0, f"Trunk freq has non-positive frequency: {tf.frequency!r}"

    def test_tgid_group_present(self, config):
        mot = next(s for s in config.systems if s.is_motorola)
        tgid_groups = [g for g in mot.groups if not g.is_site]
        assert len(tgid_groups) >= 1

    def test_tgids_loaded(self, config):
        mot = next(s for s in config.systems if s.is_motorola)
        tgids = [
            ch for g in mot.groups if not g.is_site
            for ch in g.channels
            if isinstance(ch, TalkGroup)
        ]
        assert len(tgids) == 9

    def test_tgids_have_numeric_ids(self, config):
        mot = next(s for s in config.systems if s.is_motorola)
        for g in mot.groups:
            for ch in g.channels:
                if isinstance(ch, TalkGroup):
                    assert ch.tgid.isdigit(), f"TGID {ch.tgid!r} is not numeric"

    def test_conventional_channels_present(self, config):
        conv = next(s for s in config.systems if s.is_conventional)
        total = sum(len(g.channels) for g in conv.groups)
        assert total > 0


class TestMarch996RoundTrip:
    @pytest.fixture(scope="class")
    def original(self):
        return file_996.load(MARCH_996)

    @pytest.fixture(scope="class")
    def reloaded(self, original):
        return _roundtrip(original)

    def test_system_count_preserved(self, original, reloaded):
        assert len(reloaded.systems) == len(original.systems)

    def test_channel_count_preserved(self, original, reloaded):
        assert _total_channels(reloaded) == _total_channels(original)

    def test_trunk_freq_count_preserved(self, original, reloaded):
        assert _total_trunk_freqs(reloaded) == _total_trunk_freqs(original)

    def test_tgid_count_preserved(self, original, reloaded):
        assert _total_tgids(reloaded) == _total_tgids(original)

    def test_motorola_system_type_preserved(self, original, reloaded):
        orig_mot = next(s for s in original.systems if s.is_motorola)
        rel_mot = next(s for s in reloaded.systems if s.is_motorola)
        assert rel_mot.system_type == orig_mot.system_type

    def test_trunk_freq_values_preserved(self, original, reloaded):
        orig_mot = next(s for s in original.systems if s.is_motorola)
        rel_mot = next(s for s in reloaded.systems if s.is_motorola)
        orig_freqs = sorted(tf.frequency for tf in orig_mot.trunk_frequencies)
        rel_freqs = sorted(tf.frequency for tf in rel_mot.trunk_frequencies)
        assert rel_freqs == orig_freqs

    def test_tgid_values_preserved(self, original, reloaded):
        orig_mot = next(s for s in original.systems if s.is_motorola)
        rel_mot = next(s for s in reloaded.systems if s.is_motorola)
        orig_tgids = sorted(
            ch.tgid for g in orig_mot.groups for ch in g.channels
            if isinstance(ch, TalkGroup)
        )
        rel_tgids = sorted(
            ch.tgid for g in rel_mot.groups for ch in g.channels
            if isinstance(ch, TalkGroup)
        )
        assert rel_tgids == orig_tgids

    def test_system_names_preserved(self, original, reloaded):
        for orig_sys, rel_sys in zip(original.systems, reloaded.systems):
            assert rel_sys.name == orig_sys.name

    def test_group_counts_preserved(self, original, reloaded):
        for orig_sys, rel_sys in zip(original.systems, reloaded.systems):
            assert len(rel_sys.groups) == len(orig_sys.groups)
