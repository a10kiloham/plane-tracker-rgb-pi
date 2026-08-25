"""Tests for utilities/flightstats.py callsign parsing (no network)."""
from utilities.flightstats import _parse_callsign


def test_icao_prefix_mapped():
    assert _parse_callsign("UAL1234") == ("UA", "1234")
    assert _parse_callsign("BAW123") == ("BA", "123")
    assert _parse_callsign("RYR81LM") == (None, None)  # alphanumeric suffix unsupported


def test_iata_prefix_passthrough():
    assert _parse_callsign("UA1234") == ("UA", "1234")
    assert _parse_callsign("B6555") == ("B6", "555")


def test_unknown_icao_falls_through():
    assert _parse_callsign("XXX999") == ("XXX", "999")


def test_garbage():
    assert _parse_callsign("") == (None, None)
    assert _parse_callsign(None) == (None, None)
    assert _parse_callsign("N123AB") == (None, None)
