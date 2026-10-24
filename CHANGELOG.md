# Changes

## 0.3.0 (unreleased)

Twelve new actions for scan-to-print, repair, casting and mold making:

- **Check if a mold can be made** (`analyse_mold`): Analyses a part for casting
  feasibility — reports how many mold pieces are needed, flags tiny details that
  won't print in the cavity, and warns about walls that would grip the cast part.
  Simple shapes return a one-click green light; complex shapes get a plain-English
  checklist with sensible defaults.
- **Make a casting mold** (`make_mold`): Builds a split mold ready to print in FDM
  or resin — two (or more) halves with a tapered pour hole, air-vent channels,
  alignment pins and bolt holes around the parting flange. Optional printable
  C-clamps (print two copies) hold the halves together while the material sets.
  Automatically smooths sub-nozzle details for a cleaner cavity, chooses wall
  thickness from the part size, and lays all pieces side-by-side in the viewer.
  Parts with undercuts get a 3-piece mold automatically: top piece plus two
  side pieces that pull out sideways to release the undercut.

- **Even out triangles** (`remesh`): Rebuilds the mesh so all triangles are
  roughly the same size — evenly spaced across the whole surface. Useful after
  scanning (where the scanner packs triangles tightly in some areas and leaves
  gaps in others), before sculpting, or before printing a surface with very
  fine detail. Shape stays the same; only the triangle layout changes.
- **Photo-guided repair** (redesigned): Now takes the photo directly as a
  base64-encoded image — no separate upload step needed. The frontend encodes
  the photo before sending it, so the user just picks the file and clicks match
  points.

Ten new actions for the scan-to-print and repair workflow:

- **Smooth scan** (`smooth_scan`): Taubin filter at light, medium or heavy strength.
  Removes scanner noise without shrinking the model.
- **Remove base** (`remove_base`): Detects the flat scan table via RANSAC-style
  horizontal face clustering and cuts it away, with an adjustable margin.
- **Seam polish** (`seam_polish`): Cleans up rough fracture surfaces after a
  join — removes micro-gaps and fills tiny holes left by the join step.
- **Fill curved** (`fill_curved`): Closes holes with a smooth patch using a
  cotangent Laplacian. Creates multi-ring fills for larger holes so the Laplacian
  can propagate curvature inward from the boundary ring. Falls back to pymeshfix
  for self-intersections the Laplacian step cannot resolve.
- **Deviation map** (`deviation_map`): Colours each vertex by its distance from
  a reference scan, letting you see exactly what the repair changed.
- **Scan to print** (`scan_to_print`): One-click chain — remove base, smooth,
  reduce detail, cleanup, make solid — producing a watertight print-ready solid.
- **Curvature map** (`curvature_map`): Colours the model by local mean curvature
  using the discrete cotangent-weighted Laplacian (Meyer et al. 2003).
  Orange = convex bumps, blue = concave dents, grey = flat.
- **Symmetry repair** (`symmetry_repair`): Detects the bilateral-symmetry plane
  via PCA and damage-score analysis, mirrors the intact half, and replaces the
  damaged side. Works for motorcycle fairings, brackets, housings, and any
  part that should be symmetric but has one side broken or missing.
  Uses manifold3d plane-split for a clean seam; falls back to face-centroid
  splitting for open/damaged input meshes.
- **Repair wound** (`repair_wound`): Designed for break wounds where the
  missing piece is lost. Smooths the jagged wound boundary with a Taubin
  pass, then fills the wound with a curvature-following patch. Different from
  Fill holes — aimed at break wounds, not scan artefacts.
- **Photo-guided repair** (`photo_repair`): Upload a photo of the intact
  part, click 4+ matching points on the photo and on the scan, and MeshRight
  estimates the camera position via DLT projection, finds regions where the
  scan is missing geometry the photo shows as present, and fills them.
  No third-party vision library needed — uses numpy, scipy and PIL.

Hardening, from a code and security audit of the whole backend:

- **Your work survives a crash.** After every step the open model is autosaved
  as a `.meshright` project in the settings folder (the newest 10 are kept).
  New `GET /api/recover` lists them and `POST /api/recover/{file}` reopens one.
  `MESHRIGHT_AUTOSAVE=0` turns it off; `sync` saves inline (for tests).
- **Undo history has a memory cap.** Older steps are dropped once the kept ones
  weigh more than `MESHRIGHT_MAX_UNDO_MB` (default 1500), not only after 20
  steps, so twenty steps on a 100 MB scan no longer hold 2 GB.
- **Batch jobs have a size cap.** A batch stops adding fixed files past
  `MESHRIGHT_MAX_BATCH_MB` (default 4000); the rest are reported as skipped
  with a plain reason instead of the server running out of memory.
- **Settings are written atomically** (temp file, fsync, rename), so a crash
  mid-write cannot leave an empty or half-written settings file.
- **AMF and 3MF are parsed with defusedxml**, so a crafted file with entity
  expansion ("billion laughs") is refused instead of eating memory.
- **`meshright fix` no longer overwrites earlier output** unless `--overwrite`
  is given; files it kept are listed on stderr.
- **Mold receipts say what was left out.** Every mold feature (pins, pour hole,
  vents, bolt holes, clamp slot) used to fail silently; the receipt now carries
  a "Left out:" line with what to do by hand.
- **Alignment pins are now actually added.** The pin union used an operator
  manifold3d does not define, so every mold since 0.3.0 began shipped without
  pins and said nothing. The matching holes also went into the wrong half.
  Pins now sit mid-wall on the negative side of each in-plane axis, clear of
  the bolt holes (which used to drill them away), and the pour hole, vents and
  bolt holes are placed from the parting face rather than the half's bounds, so
  the protruding pins no longer shift them.
- **Real-scan tests** read their models from `MESHRIGHT_TEST_MODELS` instead of
  a hard-coded NAS path, and skip cleanly when it is not set.

Three bugs found by testing on broken scans from the internet:

- **Make Solid works on tiny or metre-scale models.** A scan saved in metres
  (the Stanford bunny is 0.15 "mm" tall) was reduced to a single voxel by the
  0.2 mm grid floor and refused. Models under 30 mm now get at least 150 cells
  across. Scan to print, which ends with Make Solid, works on them too.
- **Rebuild broken end continues an open edge.** Scans of broken parts rarely
  have a flat break face; the surface just stops. Clicking next to an open
  edge now continues the surface from that edge, following its curve, and
  leaves the far end open like the edge was. Works on the open teapot and a
  cut tube; a flat face on a closed model still rebuilds a closed end.
- **Rebuild broken end no longer refuses every open scan.** Its join-up check
  demanded a watertight result, which an open scan (a face scan, an open
  bottom) can never give. It now checks that no open edges were gained or
  lost and the surface still faces one way.

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
