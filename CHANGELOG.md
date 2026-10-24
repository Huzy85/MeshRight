# Changes

## 0.2.1

- **Hollow for resin is much more reliable.** The hollowed shell now stays
  closed when saved as STL, keeps your wall thickness, and works on models
  that have a shell inside a shell or a ring shape. Pockets that would trap
  resin are found and given extra drain holes. Tiny specks that smoothing used
  to pinch off a pocket are ignored.
- **Fewer false alarms.** The self-crossing check no longer flags triangles
  whose corners sit a rounding step apart (drain holes were triggering it, and
  scores dropped from 94 to 59 for no reason). A hollowed model's inner wall
  is no longer called floating debris.
- **Cleanup removes zero-area triangles** without opening the surface, and
  says when it leaves a big opening alone because it looks like the model's own
  open edge (tick "Also close a big opening" to close it).
- Tested on real scans (Stanford bunny, horse, angel, dragon, happy Buddha) and
  ten community models: all clean up and hollow to one closed solid.

## 0.2.0

- **Ask AI** (optional): connect any AI service (OpenAI, Claude, Gemini,
  OpenRouter, DeepSeek, Mistral, Groq, or Ollama and LM Studio on your own
  computer) and ask about your model. It sees the check report, never the
  model, and nothing changes until you press Apply. Also `meshright ask`.
- **LAS and LAZ** scanner point clouds open. The downloaded app now includes
  the E57, LAS/LAZ and MCP readers.
- Fixes: undo after 20 steps, plain messages instead of raw errors, size caps
  on compressed files, MCP never overwrites without `overwrite`, non-Latin
  file names download, `meshright check` on a folder or a missing file,
  notices for one-colour and very large files, safer release workflows.
- README: 60-second start, Ask AI, troubleshooting, privacy, requirements.
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
