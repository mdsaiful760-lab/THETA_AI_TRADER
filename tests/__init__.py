"""Test package for THETA AI TRADER (new architecture, mirrors package layout).

Marking this directory as a package gives its modules fully-qualified import
names (e.g. ``tests.test_market_data_adapter``), distinct from same-named
legacy test scripts at the repository root (e.g. ``test_market_data_adapter``).
This avoids pytest's "import file mismatch" collection error when two test
files share a basename.
"""
