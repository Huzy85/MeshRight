# Changes

## 0.2.0

- **Print in colours**: a new Export item, "3MF in colours", groups a colour
  scan into 2 to 8 print colours and writes a 3MF painted for Bambu Studio,
  OrcaSlicer and PrusaSlicer (standard material colours for other viewers).
- **Works with AI apps**: `meshright mcp` is an MCP server offering check,
  Clean up, every action, Print in colours and your printer to Claude Desktop, Claude Code,
  Cursor and the like. `meshright mcp --show-config` prints the settings to
  paste. Install with `pip install "meshright[mcp]"`.
- **More files open**: USD, USDA, USDC and USDZ (iPhone Object Capture) and
  E57 scanner point clouds. Binary USD needs `pip install "meshright[usd]"`,
  E57 needs `pip install "meshright[e57]"`. FBX files get a message saying
  what to export instead.
- **Scanner recognised**: the check says which scanner app made a file
  (Revopoint, Creality, Shining 3D, Polycam, Scaniverse, KIRI, Luma and
  others) and what its scans usually need.
- **Pin clearance** is a printer setting (0.1 to 0.6 mm) and every cut
  receipt says which clearance it used.
- For contributors: `tools/check_library.py` runs the batch fix over public
  test models and writes a results table; releases fail when a packaged app
  grows past 180 MB.

## 0.1.2

- **Show what changed**: a switch under History colours the model green where
  it is as in the original file and red where repairs moved the surface.

## 0.1.1

- Too big for your printer? The checklist offers both Shrink to fit and Split
  into parts.
- Cut parts are numbered in assembly order, engraved on a hidden cut face,
  and MeshRight says which parts join.
- Stand it up can add a flat base instead of cutting the bottom off.
- Flexible preset (TPU) with a stricter wall check.
- Cancel while a big file opens, and a memory warning for very big files.
- A coin beside the model for scale.
- The Windows app builds again.

## 0.1.0

First public version: print-readiness check, one-click Clean up with presets,
Make Solid, orientation tools, printer fit and split to fit, editing tools
(erase, thicken, cut, holes, measure, walls, hollow, bust, sculpt, make a
part, combine, merge scans), colours, several models at once, batch mode,
open in slicer, and one-file apps for Windows, macOS and Linux.
