"""Whole-preset transfer: the file <-> unit converter against a preset the app itself
imported (tests/live_1d.*: the app's export and the unit's stored bytes, 2026-10-19), and
live_push_preset's refusals against a fake unit."""
import json
import os

import pytest

from helix_stadium_mcp.codec import read_hsp, write_hsp
from helix_stadium_mcp.live import convert as C
from helix_stadium_mcp.tools import live as T

HERE = os.path.dirname(__file__)
HSP = open(os.path.join(HERE, "live_1d.hsp"), "rb").read()
BIN = open(os.path.join(HERE, "live_1d.bin"), "rb").read()
ENTRY = {"cid_": 244, "name": "MCP Commands", "posi": 3, "blck": 8, "flow": 0}
PRODUCT = {"host": {"id__": 0x00260000, "vers": {"majo": 1, "mino": 4, "patc": 0, "buld": 1341}}}


def test_repack_is_byte_identical():
    assert C.pack(C.unpack(BIN)) == BIN


def test_attrs_match_the_app():
    # capture 6: the app's /SetContentAttrs for this preset
    assert C.attrs(read_hsp(HSP)).hex() == ("84a4626c636b08a4636f6c7201a4666c6f7700a46e616d65ac"
                                            "4d435020436f6d6d616e6473")


@pytest.mark.install
def test_converter_matches_the_app_both_ways():
    from helix_stadium_mcp.config import resolve_res_dir
    from helix_stadium_mcp.controls import load_command_defs
    from helix_stadium_mcp.modeldefs import load_modeldefs
    md, cd = load_modeldefs(), load_command_defs(str(resolve_res_dir()), 0x00260000)
    assert C.to_unit(read_hsp(HSP), md, cd) == BIN
    assert write_hsp(C.to_hsp(BIN, ENTRY, PRODUCT, md, cd)) == HSP


def test_unknown_things_are_refused():
    with pytest.raises(ValueError):
        C._loc(0x0101010B)                  # A12: never seen
    with pytest.raises(ValueError):
        C._get(C.FS_COLOR, "purple", "LED color")
    assert C._sid(*C._loc(0x02020100)) == 0x02020100


# --- live_push_preset against a fake unit ------------------------------------------------

class FakeUnit:
    def __init__(self, entries, active=245, content=b"old", readback=None):
        self.entries, self.active, self.stored = entries, active, content
        self.readback, self.writes = readback, []

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *e):
        pass

    def product(self):
        return PRODUCT

    def user_entries(self):
        return self.entries

    def property(self, key):
        return self.active

    def content(self, cid):
        return self.readback if (self.writes and self.readback is not None) else self.stored

    def store(self, cid, attrs, blob):
        self.writes.append((cid, attrs, blob))
        self.stored = blob
        if self.entries.get(cid):
            self.entries[cid] = {**self.entries[cid], "name": json.loads(SIDE)["name"]}


SIDE = json.dumps({"name": "MCP Commands", "blck": 8, "flow": 0, "posi": 3, "cid_": 244})


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def deco(fn):
            self.tools[name] = fn
            return fn
        return deco


@pytest.fixture
def push(tmp_path, monkeypatch):
    src = tmp_path / "restore.bin"
    src.write_bytes(BIN)
    (tmp_path / "restore.json").write_text(SIDE)
    monkeypatch.setenv("HELIX_STADIUM_PROTECT", "241")
    monkeypatch.delenv("HELIX_MCP_READONLY", raising=False)

    def run(unit, **kw):
        monkeypatch.setattr(T.L, "Unit", unit)
        mcp = FakeMCP()
        T.register(mcp, ctx=None)
        return mcp.tools["live_push_preset"](path=str(src), **kw)
    return run, tmp_path


def entries():
    return {241: {"cid_": 241, "name": "Keep", "posi": 0, "blck": 2, "flow": 1},
            244: dict(ENTRY)}


@pytest.mark.parametrize("preset,active,why", [(999, 245, "isn't one of your"),
                                               (241, 245, "protected"),
                                               (244, 244, "loaded on the unit")])
def test_push_refusals(push, preset, active, why):
    run, _ = push
    u = FakeUnit(entries(), active=active)
    r = run(u, preset=preset, expect_name="MCP Commands", execute=True)
    assert not r["ok"] and why in json.dumps(r) and u.writes == []


def test_dry_run_writes_nothing(push):
    run, _ = push
    u = FakeUnit(entries())
    r = run(u, preset=244)
    assert r["ok"] and "DRY RUN" in r["summary"] and u.writes == []
    assert r["data"]["current_name"] == "MCP Commands" and r["data"]["slot"] == "1D"


def test_wrong_expected_name_writes_nothing(push):
    run, _ = push
    u = FakeUnit(entries())
    r = run(u, preset=244, expect_name="Something Else", execute=True)
    assert not r["ok"] and u.writes == []


def test_push_backs_up_then_checks(push):
    run, tmp = push
    u = FakeUnit(entries())
    r = run(u, preset=244, expect_name="MCP Commands", execute=True)
    assert r["ok"], r
    assert u.writes and u.writes[0][2] == BIN
    backup = r["data"]["backup"]
    assert open(backup, "rb").read() == b"old" and not os.access(backup, os.W_OK)


def test_bad_readback_is_reported(push):
    run, _ = push
    u = FakeUnit(entries(), readback=b"garbled")
    r = run(u, preset=244, expect_name="MCP Commands", execute=True)
    assert not r["ok"] and "restore from" in json.dumps(r)
