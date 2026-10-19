"""Convert between a preset file (.hsp JSON) and the form the unit stores (/GetContentData).

Reverse-engineered 2026-10-19 from five presets the app imported (capture 6 proved the app
sends /SetContentAttrs + /SetContentData with exactly these bytes) paired with the app's own
exports of them. The file nests controllers and snapshot values on what they drive; the unit
keeps them in numbered tables (sources, controllers, triggers, commands), allocated in one
walk: each block's on/off, then its params by name, then Command Center commands.

Anything the fixtures never showed (splits, two-model blocks, harness-param control, LED
colors other than auto/red/white, A12/B12, ...) raises ValueError instead of guessing.
"""
from __future__ import annotations

import struct

import msgpack

from ..codec.floatfmt import fround32

TAG = b"\xff\xff\xff\xffpgsm"
ROWS = 28                                   # positions per flow (0-27)

BTYPES = {"input": 8, "output": 9, "fx": 1, "amp": 5, "cab": 6, "fxloop": 12, "looper": 2}
BEHAVIOR = {"latching": 0, "momentary": 1, "continuous": 2}
CTRL_TYPE = {"targetbypass": 1, "param": 3}
CURVE = {"linear": 5}
FS_COLOR = {"auto": 1, "red": 2, "white": 11}
SNAP_COLOR = {"auto": 1}
INST_Z = {"FirstEnabled": 1}
CMD_TYPE = {"PresetSnapshot": 1, "Song": 2, "Looper": 3, "Utility": 4, "ExtAmp": 5, "MIDI": 6,
            "HotKey": 7}
CMD_DEFS = {"MIDI": "MIDI-FS"}              # file type -> commanddefs key, when different
# physical control -> (locl, ctxt). Footswitches: page A 0x010101ii / B 0x010102ii, locl 25+i
# (proven for i = 0-10). The rest one by one.
_OTHER_SOURCES = {0x01010500: (37, 0), 0x01020100: (42, 0), 0x01020101: (42, 1),
                  0x02020100: (43, 0)}


def _loc(sid: int) -> tuple[int, int]:
    page, i = (sid >> 8) & 0xFF, sid & 0xFF
    if sid >> 16 == 0x0101 and page in (1, 2) and i <= 10:
        return 25 + i, page
    if sid in _OTHER_SOURCES:
        return _OTHER_SOURCES[sid]
    raise ValueError(f"control {sid:#x} isn't supported for transfer yet")


def _sid(locl: int, ctxt: int) -> int:
    for sid, lc in _OTHER_SOURCES.items():
        if lc == (locl, ctxt):
            return sid
    if 25 <= locl <= 35 and ctxt in (1, 2):
        return 0x01010000 | ctxt << 8 | (locl - 25)
    raise ValueError(f"the unit uses a control ({locl}, {ctxt}) this tool doesn't know yet")


def _get(table: dict, key, what: str):
    if key not in table:
        raise ValueError(f"{what} {key!r} isn't supported for transfer yet")
    return table[key]


def _rev(table: dict, val, what: str):
    for k, v in table.items():
        if v == val:
            return k
    raise ValueError(f"the unit uses a {what} ({val!r}) this tool doesn't know yet")


def _fs_ids():
    return [(p, i, 0x01010000 | (1 if p == "a" else 2) << 8 | (i - 1))
            for p in "ab" for i in range(1, 13)]


def _keys(o):
    """Four-character string keys -> the uint32 keys the unit uses, sorted like the unit."""
    if isinstance(o, dict):
        return {k: _keys(v) for k, v in sorted(
            ((int.from_bytes(k.encode(), "big"), v) for k, v in o.items()))}
    if isinstance(o, list):
        return [_keys(x) for x in o]
    return o


def _strs(o):
    if isinstance(o, dict):
        return {(struct.pack(">I", k).decode("latin1") if isinstance(k, int) else k): _strs(v)
                for k, v in o.items()}
    if isinstance(o, list):
        return [_strs(x) for x in o]
    return o


SAVED_TAG = b"_sbepgsm"     # a preset saved on the unit from its working copy


def unpack(blob: bytes) -> dict:
    """A stored preset -> its document. One saved on the unit (SAVED_TAG) also carries its
    undo history, device info and selection (hist, dvcp, selb, self); those are dropped."""
    if blob[:8] not in (TAG, SAVED_TAG):
        raise ValueError("not a stored preset")
    d = _strs(msgpack.unpackb(blob[8:], raw=False, strict_map_key=False))
    return {k: d[k] for k in ("cg__", "pm__", "sfg_")}


def pack(d: dict) -> bytes:
    return TAG + msgpack.packb(_keys(d), use_bin_type=True)


# --- file -> unit ------------------------------------------------------------------------

def to_unit(obj: dict, md: dict, cdefs: dict) -> bytes:
    """A preset file (as read_hsp returns it) -> the bytes /SetContentData takes.
    ``md`` = load_modeldefs(), ``cdefs`` = load_command_defs(...)."""
    p = obj["preset"]
    srcs, ctrls, trgs, cmnds = [], [], [], []
    by_sid, snap_trg, ptid = {}, [], {}

    def source(sid):
        if sid not in by_sid:
            locl, ctxt = _loc(sid)
            byps = p["sources"].get(str(sid), {}).get("bypass", False)
            by_sid[sid] = {"byps": byps, "cmds": [], "ctl": [], "cnt1": 0, "cnt2": 0, "cnt3": 0,
                           "ctxt": ctxt, "id__": len(srcs) + 1, "locl": locl, "mtms": 0,
                           "mtyp": 0, "type": 1}
            srcs.append(by_sid[sid])
        return by_sid[sid]

    def trigger(**t):
        t["id__"] = len(trgs) + 1
        trgs.append(t)
        return t["id__"]

    def control(node, ctype, tid):
        c = node.get("controller")
        if not c:
            return 0
        if c.get("midisource", 0) or c.get("goid", 0):
            raise ValueError("MIDI-source controllers aren't supported for transfer yet")
        if c["type"] != ctype:              # "bypassed" isn't stored; the app derives it
            raise ValueError(f"unexpected controller type {c['type']}")
        s = source(c["source"])
        cid = len(ctrls) + 1
        ctrls.append({"behv": _get(BEHAVIOR, c["behavior"], "controller behavior"), "cid_": cid,
                      "curv": _get(CURVE, c["curve"], "curve"), "dlay": c["delay"], "goid": 0,
                      "max_": c["max"], "min_": c["min"], "thrs": c["threshold"], "tid_": tid,
                      "togl": False, "trig": s["id__"], "type": CTRL_TYPE[ctype]})
        s["ctl"].append(cid)
        return cid

    def model(name):
        rec = md.get(name)
        if not rec or "id" not in rec:
            raise ValueError(f"model {name} isn't in this install's model list")
        return rec

    def params(mrec, hparams, blk_id, harness):
        names = mrec.get("params", {})
        extra = set(hparams) - set(names)
        if extra:
            raise ValueError(f"{sorted(extra)} aren't parameters of this model")
        out = []
        # like the app: every param the model has, by name; ones the file leaves out get
        # their default (proven on three tool-built presets missing harness params)
        for n in sorted(names):
            v = hparams.get(n) or {"value": _val(names[n])}
            pid = names[n]["id"]
            cid = tid = 0
            if "controller" in v or "snapshots" in v:
                if harness:
                    raise ValueError("controlling a block-level setting (like Trails) isn't "
                                     "supported for transfer yet")
                tid = trigger(eID_=blk_id, enty=3, mmid=mrec["id"], pid_=pid, pmid=mrec["id"],
                              ppid=pid, slot=0, type=2)
                ptid[blk_id << 16 | pid] = tid
                cid = control(v, "param", tid)
                if "snapshots" in v:
                    snap_trg.append((tid, v["snapshots"], False))
            out.append({"accs": 0, "cid_": cid, "mid_": mrec["id"], "pid_": pid,
                        "snap": "snapshots" in v, "tid_": tid, "valu": v["value"]})
        return out

    def block(b, blk_id):
        if len(b["slot"]) != 1:
            raise ValueError("blocks with more than one model aren't supported for transfer yet")
        s = b["slot"][0]
        mrec = model(s["model"])
        hrec = _harness(md, mrec)
        en = b["@enabled"]
        tid = cid = 0
        if "controller" in en or "snapshots" in en:
            tid = trigger(eID_=blk_id, enty=2, mmid=mrec["id"], pid_=0, slot=0, type=1)
            cid = control(en, "targetbypass", tid)
            if "snapshots" in en:
                snap_trg.append((tid, en["snapshots"], True))
        hrns = {"cid_": 0, "enbl": int(b["harness"]["@enabled"]["value"]), "id__": hrec["id"],
                "lbid": -1, "parm": params(hrec, b["harness"].get("params", {}), blk_id, True),
                "snap": False, "tid_": 0, "vers": 0}
        mdl = {"cid_": 0, "clci": 0, "enbl": int(s["@enabled"]["value"]), "id__": mrec["id"],
               "lbid": -1, "parm": params(mrec, s["params"], blk_id, False), "snap": False,
               "tid_": 0, "vers": s["version"]}
        return {"cid_": cid, "enbl": int(en["value"]), "favo": b["favorite"], "hasb": False,
                "hrns": hrns, "id__": blk_id, "mdls": [mdl], "snap": "snapshots" in en,
                "tid_": tid, "type": _get(BTYPES, b["type"], "block type")}

    flows = []
    for fi, f in enumerate(p["flow"]):
        blks = {}
        for k, b in f.items():
            if k == "@enabled":
                continue
            pos = b["position"]
            if k != f"b{pos:02d}" or b["path"] != 0:
                raise ValueError("split paths aren't supported for transfer yet")
            blks[pos] = b
        row = []
        for pos in sorted(set(blks) | {14, 27}):
            if pos in blks:
                row += [pos, block(blks[pos], fi * ROWS + pos)]
            else:                           # the unit always stores the unused lane's I/O
                row += [pos, _empty_io(md, "P35_InputNone" if pos == 14 else "P35_OutputNone",
                                       fi * ROWS + pos)]
        flows.append({"bcnt": ROWS, "blks": row, "bmap": list(range(fi * ROWS, fi * ROWS + ROWS)),
                      "cid_": 0, "enbl": int(f["@enabled"]["value"]), "snap": False, "tid_": 0})

    for key, cl in p.get("commands", {}).items():
        s = source(int(key))
        for i, c in enumerate(cl):
            if c["ordinal"] != i:
                raise ValueError("Command Center commands out of order")
            ctype = _get(CMD_TYPE, c["type"], "command type")
            pdefs = _cparams(cdefs, c["type"])
            extra = set(c["params"]) - set(pdefs)
            if extra:
                raise ValueError(f"unknown {c['type']} param(s) {sorted(extra)}")
            mid = len(cmnds) + 1
            tid = trigger(eID_=mid, enty=6, pid_=0, slot=0, type=4)
            cm = {"behv": _get(BEHAVIOR, c["behavior"], "command behavior"), "cid_": mid,
                  "curv": _get(CURVE, c["curve"], "curve"), "dlay": c["delay"],
                  "func": c["params"].get("Command", {}).get("value", 0), "goid": c["goid"],
                  "thrs": c["threshold"], "tid_": tid, "togl": c["toggle"], "trig": s["id__"],
                  "type": ctype}
            for n, d in pdefs.items():
                letter = chr(ord("a") + d["id"])
                cm[f"psp{letter}"] = False
                cm[f"pvl{letter}"] = c["params"][n]["value"]
            cmnds.append(cm)
            s["cmds"].append(mid)

    for s in srcs:
        if len(s["cmds"]) > 2:
            raise ValueError("more than two commands on one switch isn't supported yet")
        s["cmds"] += [-1] * (2 - len(s["cmds"]))

    stid = sorted(t for t, _, _ in snap_trg)
    vals = {t: (v, byp) for t, v, byp in snap_trg}
    snps = []
    for i, sn in enumerate(p["snapshots"]):
        if sn["source"] != 0:
            raise ValueError("snapshot sources aren't supported for transfer yet")
        e = {"bpm_": sn["tempo"], "colr": _get(SNAP_COLOR, sn["color"], "snapshot color"),
             "exsw": sn["expsw"], "name": sn["name"], "si__": i, "vald": sn["valid"]}
        if sn["valid"]:
            tamv = []
            for t in stid:
                v, byp = vals[t]
                if v[i] is None:
                    raise ValueError("a valid snapshot is missing a value")
                tamv += [t, (not v[i]) if byp else v[i]]
            e.update(camv=[], tamv=tamv, tgls=[])
        snps.append(e)

    scid = []
    for s in srcs:
        scid += [s["id__"], s.pop("ctl")]
    entt = {"cmnd": cmnds, "ctm_": {"htid": [], "ptid": [x for k in sorted(ptid) for x in (k, ptid[k])],
                                    "stid": stid},
            "ctrl": ctrls, "sm__": {"scid": scid, "ssi_": []}, "snps": snps, "srcs": srcs,
            "trgs": trgs}
    cg = {"asnp": p["params"]["activesnapshot"], "entt": entt, "nxtc": len(ctrls) + 1, "nxti": 0,
          "nxtm": len(cmnds) + 1, "nxts": len(srcs) + 1, "nxtt": len(trgs) + 1}
    return pack({"cg__": cg, "pm__": _pm(obj), "sfg_": {"enbl": 1, "fcnt": len(flows), "flow": flows}})


def _cparams(cdefs, ctype):
    """A command type's params; "toggle" is a top-level field in the file, not a param."""
    return {n: d for n, d in cdefs[CMD_DEFS.get(ctype, ctype)]["params"].items() if n != "toggle"}


def _val(d):
    return fround32(float(d["def"])) if d["type"] == "f" else d["def"]


def _harness(md, mrec):
    for k, r in md.items():
        if isinstance(r, dict) and r.get("id") == mrec.get("harness"):
            return r
    raise ValueError("this model's block harness isn't in the install's model list")


def _empty_io(md, name, blk_id):
    rec = md[name]
    hrec = _harness(md, rec)
    parm = [{"accs": 0, "cid_": 0, "mid_": rec["id"], "pid_": d["id"], "snap": False, "tid_": 0,
             "valu": _val(d)} for _, d in sorted(rec["params"].items())]
    return {"cid_": 0, "enbl": 1, "favo": 0, "hasb": False,
            "hrns": {"cid_": 0, "enbl": 1, "id__": hrec["id"], "lbid": -1, "parm": [],
                     "snap": False, "tid_": 0, "vers": 0},
            "id__": blk_id, "mdls": [{"cid_": 0, "clci": 0, "enbl": 1, "id__": rec["id"],
                                      "lbid": -1, "parm": parm, "snap": False, "tid_": 0,
                                      "vers": 0}],
            "snap": False, "tid_": 0, "type": BTYPES["input" if blk_id % ROWS == 14 else "output"]}


_PM = [("preset.tempo.bpm", "f", ("params", "tempo")), ("preset.inst1.z", "i", ("params", "inst1Z")),
       ("preset.inst2.z", "i", ("params", "inst2Z")),
       ("preset.expsw.active", "i", ("params", "activeexpsw")),
       ("preset.clip.start", "f", ("clip", "start")), ("preset.clip.end", "f", ("clip", "end")),
       ("preset.clip.filename", "s", ("clip", "filename")), ("preset.clip.path", "s", ("clip", "path")),
       ("preset.xyctrl.rbtime", "f", ("xyctrl", "rbtime")),
       ("preset.xyctrl.rubberband", "i", ("xyctrl", "rubberband")),
       ("preset.xyctrl.x", "i", ("xyctrl", "x")), ("preset.xyctrl.y", "i", ("xyctrl", "y"))]


def _pm(obj):
    p = obj["preset"]
    out = []
    for key, typ, (a, b) in _PM:
        v = p[a][b]
        if b.startswith("inst"):
            v = _get(INST_Z, v, "input impedance")
        out.append({"key_": key, "type": typ, "val_": v})
    out.append({"key_": "preset.meta.info", "type": "s", "val_": obj["meta"]["info"]})
    for page, i, sid in _fs_ids():
        s = p["sources"][str(sid)]
        k = f"preset.floorboard.stomp.{page}.{i}."
        out += [{"key_": k + "color", "type": "i", "val_": _get(FS_COLOR, s["fs_color"], "LED color")},
                {"key_": k + "label", "type": "s", "val_": s["fs_label"]},
                {"key_": k + "topidx", "type": "i", "val_": s["fs_topidx"]}]
    return out


def attrs(obj: dict) -> bytes:
    """The /SetContentAttrs blob: the name, color and where the cursor was (capture 6)."""
    cur = obj["preset"]["cursor"]
    _get(SNAP_COLOR, obj["meta"]["color"], "preset color")
    return pack_attrs(obj["meta"]["name"], cur["position"], cur["flow"])


def pack_attrs(name: str, blck: int, flow: int) -> bytes:
    # ponytail: colr is always 1 (auto); the only preset color seen. Map more when one shows up.
    return msgpack.packb({"blck": blck, "colr": 1, "flow": flow, "name": name})


# --- unit -> file ------------------------------------------------------------------------

def to_hsp(blob: bytes, entry: dict, product: dict, md: dict, cdefs: dict) -> dict:
    """The unit's stored preset -> a preset file dict (write it with write_hsp).
    ``entry`` = its /GetContainerContents row (name, cursor); ``product`` = /ProductInfoGet."""
    d = unpack(blob)
    cg, e = d["cg__"], d["cg__"]["entt"]
    if e["ctm_"]["htid"] or e["sm__"]["ssi_"]:
        raise ValueError("this preset uses settings this tool can't transfer yet")
    by_id = {r["id"]: (k, r) for k, r in md.items() if isinstance(r, dict) and "id" in r}
    srcs = {s["id__"]: s for s in e["srcs"]}
    ctrls = {c["tid_"]: c for c in e["ctrl"]}
    stid = e["ctm_"]["stid"]
    snaps = e["snps"]

    def snapvals(tid, byp):
        out = []
        for s in snaps:
            if not s["vald"]:
                out.append(None)
                continue
            t = s["tamv"]
            v = dict(zip(t[0::2], t[1::2]))[tid]
            out.append((not v) if byp else v)
        return out

    def controller(tid, ctype):
        c = ctrls[tid]
        s = srcs[c["trig"]]
        if c["goid"] or c["togl"]:
            raise ValueError("this preset uses controller settings this tool can't transfer yet")
        return {"behavior": _rev(BEHAVIOR, c["behv"], "controller behavior"),
                "bypassed": ctype == "param", "curve": _rev(CURVE, c["curv"], "curve"),
                "delay": c["dlay"], "goid": 0, "max": c["max_"], "midisource": 0, "min": c["min_"],
                "source": _sid(s["locl"], s["ctxt"]), "threshold": c["thrs"],
                "type": ctype}

    def node(value, tid, ctype, byp):
        n = {}
        if tid and tid not in ctrls and tid not in stid:
            raise ValueError(f"trigger {tid} is neither a controller nor a snapshot value")
        if tid and tid in ctrls:
            n["controller"] = controller(tid, ctype)
        if tid and tid in stid:
            n["snapshots"] = snapvals(tid, byp)
        n["value"] = value
        return n

    def params(m, names):
        out = {}
        for q in m["parm"]:
            n = names.get(q["pid_"])
            if n is None:
                raise ValueError(f"unknown parameter {q['pid_']} on model {m['id__']}")
            out[n] = node(q["valu"], q["tid_"], "param", False)
        return out

    flows = []
    for f in d["sfg_"]["flow"]:
        hf = {"@enabled": {"value": bool(f["enbl"])}}
        for pos, b in zip(f["blks"][0::2], f["blks"][1::2], strict=True):
            mname, mrec = by_id[b["mdls"][0]["id__"]]
            if pos in (14, 27) and mname in ("P35_InputNone", "P35_OutputNone"):
                continue
            if len(b["mdls"]) != 1 or b["type"] in (3, 4) or 14 <= pos <= 27:
                raise ValueError("split paths and two-model blocks aren't supported for "
                                 "transfer yet")
            m = b["mdls"][0]
            hname, hrec = by_id[b["hrns"]["id__"]]
            hb = {"@enabled": node(bool(b["enbl"]), b["tid_"], "targetbypass", True)}
            btype = _rev(BTYPES, b["type"], "block type")
            if btype in ("input", "output"):
                hb["endpoint"] = "b13" if btype == "input" else "b00"
            hb["favorite"] = b["favo"]
            harn = {"@enabled": {"value": bool(b["hrns"]["enbl"])}}
            hp = params(b["hrns"], {p["id"]: n for n, p in hrec.get("params", {}).items()})
            if hp:
                harn["params"] = hp
            hb.update(harness=harn, path=0, position=pos,
                      slot=[{"@enabled": {"value": bool(m["enbl"])}, "model": mname,
                             "params": params(m, {p["id"]: n for n, p in mrec["params"].items()}),
                             "version": m["vers"]}],
                      type=btype)
            hf[f"b{pos:02d}"] = hb
        flows.append(hf)

    pm = {x["key_"]: x["val_"] for x in d["pm__"]}
    sources = {}
    fs = {sid: (page, i) for page, i, sid in _fs_ids()}
    used = {_sid(s["locl"], s["ctxt"]): s for s in e["srcs"]}
    for sid in sorted(set(fs) | set(used)):
        v = {}
        if sid in used:
            v["bypass"] = used[sid]["byps"]
        if sid in fs:
            page, i = fs[sid]
            k = f"preset.floorboard.stomp.{page}.{i}."
            v.update(fs_color=_rev(FS_COLOR, pm[k + "color"], "LED color"),
                     fs_label=pm[k + "label"], fs_topidx=pm[k + "topidx"])
        sources[str(sid)] = v

    commands = {}
    for s in e["srcs"]:
        for ordinal, mid in enumerate(x for x in s["cmds"] if x != -1):
            c = next(c for c in e["cmnd"] if c["cid_"] == mid)
            ctype = _rev(CMD_TYPE, c["type"], "command type")
            pdefs = _cparams(cdefs, ctype)
            commands.setdefault(str(_sid(s["locl"], s["ctxt"])), []).append({
                "behavior": _rev(BEHAVIOR, c["behv"], "command behavior"),
                "curve": _rev(CURVE, c["curv"], "curve"), "delay": c["dlay"], "goid": c["goid"],
                "ordinal": ordinal,
                "params": {n: {"value": c["pvl" + chr(ord("a") + pd["id"])]}
                           for n, pd in sorted(pdefs.items())},
                "threshold": c["thrs"], "toggle": c["togl"], "type": ctype})

    preset = {"clip": {"end": pm["preset.clip.end"], "filename": pm["preset.clip.filename"],
                       "path": pm["preset.clip.path"], "start": pm["preset.clip.start"]}}
    if commands:
        preset["commands"] = dict(sorted(commands.items()))
    preset["cursor"] = {"flow": entry["flow"], "path": 0, "position": entry["blck"]}
    preset["flow"] = flows
    preset["params"] = {"activeexpsw": pm["preset.expsw.active"], "activesnapshot": cg["asnp"],
                        "inst1Z": _rev(INST_Z, pm["preset.inst1.z"], "input impedance"),
                        "inst2Z": _rev(INST_Z, pm["preset.inst2.z"], "input impedance"),
                        "tempo": pm["preset.tempo.bpm"]}
    preset["snapshots"] = [{"color": _rev(SNAP_COLOR, s["colr"], "snapshot color"),
                            "expsw": s["exsw"], "name": s["name"], "source": 0,
                            "tempo": s["bpm_"], "valid": s["vald"]} for s in snaps]
    preset["sources"] = sources
    preset["xyctrl"] = {"rbtime": pm["preset.xyctrl.rbtime"],
                        "rubberband": pm["preset.xyctrl.rubberband"],
                        "x": pm["preset.xyctrl.x"], "y": pm["preset.xyctrl.y"]}
    v = product["host"]["vers"]
    meta = {"color": "auto", "device_id": product["host"]["id__"],
            "device_version": v["majo"] << 28 | v["mino"] << 24 | v["patc"] << 16 | v["buld"],
            "info": pm["preset.meta.info"], "name": entry["name"]}
    return {"meta": meta, "preset": preset}
