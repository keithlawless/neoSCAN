"""Tests for GLG (reception status) parsing and squelch gating."""
from app.serial.protocol import ScannerProtocol


def _proto(payload):
    """A ScannerProtocol whose send_command returns a canned GLG payload."""
    p = ScannerProtocol(conn=None)  # conn unused; send_command is stubbed
    p.send_command = lambda cmd, *a: payload
    return p


# GLG fields: FRQ,MOD,ATT,CTCSS,NAME1,NAME2,NAME3,SQL,MUT,SYS_TAG,CHAN_TAG,RVS
def test_receiving_squelch_open_returns_info():
    info = _proto("0154237500,FM,0,0,Public Safety,Boroughs,Westboro PD,1,0,,,").get_received_channel_info()
    assert info is not None
    assert info["frequency"] == "0154237500"
    assert info["ch_name"] == "Westboro PD"
    assert info["sql"] == "1"


def test_hang_time_squelch_closed_returns_none():
    # Frequency is still present (scanner still on the channel) but squelch is
    # closed — this is the post-transmission hang/delay, not live audio.
    info = _proto("0154237500,FM,0,0,Public Safety,Boroughs,Westboro PD,0,0,,,").get_received_channel_info()
    assert info is None


def test_idle_all_empty_returns_none():
    info = _proto("GLG,,,,,,,,,,").get_received_channel_info()
    # send_command strips the echoed "GLG," prefix; simulate the payload directly
    info = _proto(",,,,,,,,,,").get_received_channel_info()
    assert info is None


def test_missing_sql_field_falls_back_to_frequency():
    # Short response without an SQL field must not be misread as squelch-closed.
    info = _proto("0154237500,FM,0").get_received_channel_info()
    assert info is not None
    assert info["frequency"] == "0154237500"


def test_ng_response_returns_none():
    assert _proto("NG").get_received_channel_info() is None
