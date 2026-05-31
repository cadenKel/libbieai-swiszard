"""Smoke tests for rule-based routing (no model load required)."""
from swiszard.router import _route_by_rules


def test_shell_routes_on_backticks():
    assert _route_by_rules("run BACKTICK ls -la BACKTICK".replace("BACKTICK", chr(96))) == "handler_shell"


def test_memory_recall_routes():
    assert _route_by_rules("memory recall foo") == "handler_memory"
    assert _route_by_rules("recall the thing") == "handler_memory"


def test_memory_does_not_hijack_paths():
    assert _route_by_rules("read /home/x/.hermes/memories/MEMORY.md") == "handler_file_read"


def test_file_read_routes():
    assert _route_by_rules("read /etc/hostname") == "handler_file_read"


def test_file_find_routes():
    assert _route_by_rules("find files matching foo in /tmp") == "handler_file_find"


def test_write_b64_routes():
    assert _route_by_rules("write_b64 /tmp/x.txt aGVsbG8=") == "handler_file_write"


def test_web_search_routes():
    assert _route_by_rules("search the web for foo") == "handler_web_search"
