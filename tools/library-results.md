# Library results

MeshRight `tools/check_library.py`, balanced preset, 2026-10-07.
Scores are print-readiness out of 100, before and after the one-click cleanup.

| Model | Triangles | Before | After | Ready | Seconds | Problems left |
|---|---:|---:|---:|:---:|---:|---|
| busted | 3,878 | 95 | 95 | yes | 0.1 | May tip over on the bed |
| soup | 100 | 59 | 59 | no | 0.0 | 100 holes in the surface |
| multibody | 32 | 90 | 100 | yes | 0.0 | none |
| fuze | 1,000 | 100 | 100 | yes | 0.0 | none |
| plate_holes | 1,252 | 100 | 100 | yes | 0.1 | none |
| featuretype | 3,476 | 100 | 100 | yes | 0.3 | none |
| not_convex | 96 | 59 | 59 | no | 0.0 | 12 non-manifold edges |
| teapot | 894 | 59 | 100 | yes | 0.0 | none |
| rabbit | 1,252 | 95 | 95 | yes | 0.0 | May tip over on the bed |
| fixed_top | 4,772 | 100 | 100 | yes | 0.2 | none |
| suzanne | 968 | 59 | 100 | yes | 0.0 | none |
| quadknot | 2,880 | 95 | 95 | yes | 0.1 | May tip over on the bed |
| tube | 120 | 100 | 100 | yes | 0.0 | none |
| points_emptyface | 3,252 | 100 | 100 | yes | 0.1 | none |
| broken-scan | 20,000 | 59 | 95 | yes | 0.4 | May tip over on the bed |
| face-relief | 11,092 | 59 | 59 | no | 0.6 | 1 hole in the surface |
| scan-on-table | 4,304 | 59 | 59 | no | 0.5 | 1 hole in the surface |
| bunny | 99,785 | 25 | 100 | yes | 4.6 | none |

## What each model is

- **busted**: self-crossing surface (MIT (trimesh test models))
- **soup**: loose triangles with no shared edges (MIT (trimesh test models))
- **multibody**: several pieces in one file (MIT (trimesh test models))
- **fuze**: textured scan of a bottle with an open bottom (MIT (trimesh test models))
- **plate_holes**: plate with through holes (MIT (trimesh test models))
- **featuretype**: CAD part with many features (MIT (trimesh test models))
- **not_convex**: concave shape (MIT (trimesh test models))
- **teapot**: classic teapot with open spout and lid gaps (MIT (trimesh test models))
- **rabbit**: low-poly rabbit scan (MIT (trimesh test models))
- **fixed_top**: mesh with a patched top (MIT (trimesh test models))
- **suzanne**: Blender monkey head with open eyes (MIT (trimesh test models))
- **quadknot**: knot made of quads (MIT (trimesh test models))
- **tube**: open tube (MIT (trimesh test models))
- **points_emptyface**: a point cloud with no triangles (MIT (trimesh test models))
- **broken-scan**: built-in sample (MeshRight sample (made in code))
- **face-relief**: built-in sample (MeshRight sample (made in code))
- **scan-on-table**: built-in sample (MeshRight sample (made in code))
- **bunny**: scan with holes in the base (Stanford bunny scan, shipped with pymeshfix)

## Steps taken

- **busted**: Cleanup found nothing to change
- **soup**: Cleanup: filled 100 holes (largest 1.31 mm)
- **multibody**: Cleanup: turned wrong-way triangles outwards
- **fuze**: Cleanup found nothing to change
- **plate_holes**: Cleanup found nothing to change
- **featuretype**: Cleanup found nothing to change
- **not_convex**: Cleanup found nothing to change
- **teapot**: Cleanup: filled 6 holes (largest 38.9 mm)
- **rabbit**: Cleanup found nothing to change
- **fixed_top**: Cleanup found nothing to change
- **suzanne**: Cleanup: filled 4 holes (largest 0.774 mm)
- **quadknot**: Cleanup found nothing to change
- **tube**: Cleanup found nothing to change
- **points_emptyface**: Cleanup found nothing to change
- **broken-scan**: Cleanup: deleted 1 loose piece; filled 2 holes (largest 25 mm)
- **face-relief**: Cleanup found nothing to change
- **scan-on-table**: Cleanup: repaired 33 triangles that cut through the surface; the rest of the surface moved less than 0.35 mm
- **bunny**: Cleanup: deleted 66 loose pieces; filled 5 holes (largest 15 mm); repaired 2 triangles that cut through the surface; the rest of the surface moved less than 0.17 mm
