# What this fork adds, and what it covers

This is [rpossum/helix-stadium-mcp](https://github.com/rpossum/helix-stadium-mcp), a fork of
[TwelveTake-Studios/helix-stadium-mcp](https://github.com/TwelveTake-Studios/helix-stadium-mcp)
by Dave Cilluffo. It is kept in sync with upstream (currently through v0.2.3) and adds about
1,750 lines on top: 20 commits, all tested on a Helix Stadium XL running firmware 1.4.0.

Upstream works on `.hsp` files. This fork adds two things upstream doesn't have:

1. **Live control of the unit over the network** (no files).
2. **Footswitches, expression pedals, labels and Command Center** in `.hsp` files.

Tool-by-tool usage is in the [README](../README.md). This page is the inventory: what the fork
added, what it was checked against, and what it still can't do.

## 1. Live control (`live_*` tools)

The tools talk to the unit the way the Helix Stadium desktop app does: TCP port 2002, set by
`HELIX_STADIUM_HOST`. The protocol was worked out from packet captures of the app; Line 6 doesn't
publish it.

| You can do this live | Tool |
|---|---|
| Read what's loaded: every block, on/off, every parameter | `live_read` |
| Turn any knob, switch or list setting (cab mic, note value) | `live_set_param` |
| Turn a block on or off | `live_toggle_block` |
| Add, swap, move or delete a block (effects, amps, preamps, cabs, IRs, FX loops, sends, returns) | `live_add_block`, `live_replace_block`, `live_move_block`, `live_remove_block` |
| Split a path, change the split type (Y, A/B, crossover, dynamic), merge the lanes | `live_add_split`, `live_set_split_type`, `live_remove_split`, `live_add_merge`, `live_remove_merge` |
| Change a path's input or output | `live_set_io` |
| Switch snapshot 1–8 | `live_snapshot` |
| List stored presets, load one, save over the loaded user preset | `live_list_presets`, `live_load_preset`, `live_save_preset` |
| Copy a whole stored preset to an `.hsp` file, or put an `.hsp` onto a user slot (no app needed) | `live_pull_preset`, `live_push_preset` |

Safety:
- Every change is read back from the unit.
- The preset and the block are re-checked right before each write, so an edit never lands on the
  wrong preset if someone changes it on the floor.
- Saving refuses factory presets, then reloads the preset to confirm the save took.
- Pushing a whole preset is a dry run unless `execute` is set, and needs the slot's current name
  (`expect_name`). It refuses factory slots, the loaded preset, and anything in
  `HELIX_STADIUM_PROTECT` (comma-separated preset ids). The old preset is saved read-only to
  `.helix-backups/` next to the file first, and the new one is read back byte for byte. A backup
  `.bin` (with its `.json`) can be pushed straight back to restore.

**How it was verified.** `scripts/live_sweep.py` added each of the 741 effect, amp, preamp, cab,
IR, loop, send and return models live. It set every parameter to its minimum, maximum and default
and read each one back, with no failures. Every input and output choice, all four split types and
the merge were checked the same way.

## 2. Controllers in `.hsp` files

| Setting | Tool |
|---|---|
| A block or a parameter (min/max) on Stomp A1–A12, B1–B12 or the toe switch; latching or momentary | `assign_footswitch` |
| A parameter swept by EXP 1 or EXP 2 between a min and max | `assign_expression` |
| Take a block or parameter off its switch or pedal | `unassign_controller` |
| Scribble-strip text and LED color | `set_switch_label` |
| "Snapshots Set Bypass" per block | `set_snapshot_bypass` |
| Make a parameter snapshot-controlled, or not | `set_param_snapshot_control` |
| Command Center: MIDI, preset/snapshot and other commands on a switch | `add_command`, `clear_commands` |

`explain_preset` now names switches and pedals (Stomp A1, EXP1, TOE) and shows labels, LED colors
and Command Center commands.

**How it was verified.** The formats were worked out from presets exported off the unit. Replay
tests check that the tools write the same bytes the unit does, using exports kept in `captures/`.
Seven full presets built with these tools load on the unit.

## 3. Smaller changes

- Expression-pedal assignments are no longer reported as "disabled". The unit marks every new pedal
  assignment `bypassed`, which upstream read as off.
- The write gate refuses to overwrite a read-only `.hsp`, so marking an exported original
  read-only protects it.

## What the tool does NOT cover yet

| Setting | Status |
|---|---|
| **Trails** (delay/reverb spill-over) | Not settable. It's stored on the block (`harness.params.Trails`), so it would be a small addition. Set it in the app for now. |
| **Input impedance ("Z", e.g. First Block)** | Not settable. It's greyed out in the app too. |
| **Preset tempo, tap tempo, tempo sync of time-based effects** | Tempo is read but can't be changed. |
| **Footswitches, pedals, labels, Command Center on the live unit** | Edit the `.hsp`, then `live_push_preset` it. |
| **Whole-preset push/pull of some presets** | Refused rather than guessed: split paths, two-model blocks, a harness parameter on a controller, LED colors other than auto/red/white, Stomp A12/B12, a preset color other than auto, more than two commands on one switch, snapshot-switched commands, and a few controller sources not yet seen (e.g. one on 1B). Checked byte for byte against the app on 9 stored presets. |
| **Renaming a preset or snapshot on the live unit** | Rename in the file, then `live_push_preset`. |
| **Setlists stored on the unit** | `build_setlist` makes a file and checklist; it doesn't arrange the unit's setlists. |
| **Global settings** (I/O levels, pedal calibration, display, MIDI channel) | Not covered. |
| **Proxy captures, IR import** | Not covered. Both are done on the unit or in the app. |
| **Firmware other than 1.4.0, other Stadium models, macOS** | Untested. The formats come from one XL on 1.4.0. |

Known quirks:
- The Bucket Brigade delay tops out at 300 ms and quietly clamps anything longer.
- List-type parameters given to `edit_param` take an index (BitDepth 7 = 24-bit), not the label.
- The unit drops a scribble-strip label from a switch with nothing assigned to it.
- Don't run `live_sweep.py` with the desktop app open; the app crashed once during a sweep.

## Contributing back upstream

The plan is to offer these to upstream as small, separate pull requests, after asking the
maintainer whether he wants them:

1. The expression "disabled" fix and the read-only guard (small, standalone).
2. Footswitch / expression / label / snapshot-control tools.
3. Command Center.
4. Live control, which is the largest piece and depends on a reverse-engineered protocol, so it may
   be better offered as an optional extra.

Before fixing a bug here, check upstream first (`git fetch upstream`, then
`git log main..upstream/main`); it may already be fixed. Create issues and PRs with
`-R rpossum/helix-stadium-mcp`, because a bare `gh` command in this checkout targets upstream.
