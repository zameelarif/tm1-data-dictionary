"""Sanity tests for package metadata."""

from tm1_data_dictionary import __version__


def test_version_is_string() -> None:
    assert isinstance(__version__, str)
    assert __version__.count(".") == 2  # semver X.Y.Z
