# Spike notes — Oct 6, 2026

A one-off measurement run inside Fusion that decided the render approach,
the capture format and the tone handling. The command that produced these
numbers was removed once the decisions were made; the numbers and captures
stay here as the record of what Fusion does.

## Environment
- sys.version: `3.14.0 (v3.14.0:ebf955df7a8, Oct  7 2025, 08:20:14) [Clang 16.0.0 (clang-1600.0.26.6)]`
- platform: `macOS-26.5-arm64-arm-64bit-Mach-O`
- Fusion version: `2705.1.25`
- viewport: 3420 × 1277 px, visual style 2
- RENDER_PX = 2400, PART_GRAY = 227, MASK_GRAY = 64
- document: "Main Body", isSaved=True, documents open: 12

## Body
- name: Body1; proxy: False; faces: 43; area: 42.3952 cm²
- compute_part_geometry (no masks): 0.06 s
- test masked face: index 2, plane, 0.64 sq in, normal z +1.00
- compute_part_geometry (with the test mask): 0.03 s
- envelope (in): 2.3583 × 2.3583 × 0.2264; surface area: 6.57 sq in; auto view from +Z: True; test masked faces matched: [2]
- Matrix3D.asArray() is row-major with translation in the last column: True
- canonical copy bbox (cm): min (-2.995, -2.995, -0.288) max (2.995, 2.995, 0.288); expected ±(2.995, 2.995, 0.288)

## Throwaway document
- Documents.add: 0.10 s; name "Untitled"; isSaved=False; documents open: 13
- designType after set: 0 (DirectDesignType = 0)
- designIntent (preview API): 2
- BRepBodies.add: ok; faces 43
- Appearance "BivyAnodize Part Gray" copied from "Plastic - Matte (Yellow)" (Prism-129) via property opaque_albedo.
- Appearance "BivyAnodize Mask Gray" copied from "Plastic - Matte (Yellow)" (Prism-129) via property opaque_albedo.
- properties of the copied appearance: interior_model/Material Type/IntegerProperty, opaque_albedo/Color/ColorProperty, opaque_albedo_colorspace/Color space/ChoiceProperty, opaque_albedo_urn/URN/StringProperty, opaque_emission/Emission/BooleanProperty, opaque_f0/Reflectance/FloatProperty, opaque_f0_urn/URN/StringProperty, opaque_luminance/Emissive Luminance/FloatProperty, opaque_luminance_modifier/Luminance Scale/ColorProperty, opaque_luminance_modifier_colorspace/Color space/ChoiceProperty, opaque_luminance_modifier_urn/URN/StringProperty, opaque_mfp/Depth/FloatProperty, opaque_mfp_modifier/Weight/ColorProperty, opaque_mfp_modifier_colorspace/Color space/ChoiceProperty, opaque_mfp_modifier_urn/URN/StringProperty, opaque_translucency/Translucency/BooleanProperty, surface_albedo/Color/ColorProperty, surface_albedo_colorspace/Color space/ChoiceProperty, surface_albedo_urn/URN/StringProperty, surface_anisotropy/Anisotropy/FloatProperty, surface_anisotropy_urn/URN/StringProperty, surface_cutout/Image/AppearanceTextureProperty, surface_cutout_urn/URN/StringProperty, surface_ndf_type/NDF/ChoiceProperty, surface_normal/Image/AppearanceTextureProperty, surface_normal_urn/URN/StringProperty, surface_rotation/Rotation/FloatProperty, surface_rotation_urn/URN/StringProperty, surface_roughness/Roughness/FloatProperty, surface_roughness_urn/URN/StringProperty
- masked test face matched on the added body: [2]
- plan camera extents after fit (cm): (6.416679458533139, 6.416679458533139); capture size 2400 × 931

| format | written | bytes | capture s | decode s | alpha usable | mean gray | light frac | dark frac | note |
|---|---|---|---|---|---|---|---|---|---|
| png | True | 73360 | 0.11 | 0.10 | True | 130 | 0.03 | 0.43 | png rows: sub=348, up=568, average=1, paeth=14; unfilter s: sub=0.0524, up=0.0137, average=0.0006, paeth=0.0141 |
| bmp | False | 0 | 0.00 | 0.00 | False | 0 | 0.00 | 0.00 | capture failed: Viewport.saveAsImageFileWithOptions failed for plan_transparent.bmp |
| tif | True | 72754 | 0.06 | 0.00 | False | 0 | 0.00 | 0.00 | decode failed: unsupported TIFF compression 8 |
| tiff | True | 72354 | 0.04 | 0.00 | False | 0 | 0.00 | 0.00 | decode failed: unsupported TIFF compression 8 |
| jpg | True | 181998 | 0.03 | 0.00 | False | 0 | 0.00 | 0.00 | decode failed: JPEG has no alpha channel and is not supported |

- grid dodge along z: captured plan_dodge_z.png (body moved 12.0 cm)
- grid dodge along x: captured plan_dodge_x.png (body moved 12.0 cm)
- scratch document isModified before close: True; isSaved: False
- Document.close(False): True in 0.10 s; documents open: 12 (was 12)
- original document active again: True

## In-place fallback (custom graphics)
- in-place render failed: module 'adsk.core' has no attribute 'CustomGraphicsSolidColorEffect'

## Automatic reading of the results
- Capture formats that keep alpha: png. Recommended CAPTURE_FORMAT: `png` (first uncompressed format with alpha; PNG otherwise).
- PNG decode time at 2400 px: 0.10 s (threshold ~4 s; lower RENDER_PX to 1800 if it is slower).
- Appearance overrides render in captures: body gray NO, masked-face gray YES.
- Grid: compare spike_out/plan_transparent.png with plan_opaque_background.png and the plan_dodge_*.png captures. If grid lines show in the transparent capture, set GRID_DODGE_AXIS in config.py to the axis whose dodge capture is clean.
- Data Panel: check now and again after two minutes that no new design appeared. If one did, set RENDER_APPROACH = 'inplace' in config.py.
- Recommended RENDER_APPROACH: `throwaway` if the Data Panel stays clean and the captures look right, else `inplace` (custom graphics did not appear in the fallback capture).

The captures are kept in `BivyAnodizeSpec/reference/spike-2026-10-06/`.

## Decisions taken from these results (Oct 6, 2026)

- **Render approach: throwaway document.** It opened, rendered and closed in
  well under a second, the document count returned to 12, the original
  document became active again, and nothing appeared in the Data Panel.
- **Capture format: PNG** (`CAPTURE_FORMAT = 'png'`). PNG keeps the alpha
  channel and decodes in about 0.1 s (almost all rows use the Sub/Up filters).
  TIFF is written with Deflate compression and also keeps alpha; the decoder
  now supports Deflate, so it is the second probe candidate. BMP is not
  written by Fusion; JPEG has no alpha.
- **Grid: not present** in transparent captures (it shows only with an
  opaque background), so `GRID_DODGE_AXIS` stays empty. The dodge captures
  were empty apart from the pivot marker and were removed from the spike.
- **Tones.** The body rendered warm beige (213, 208, 195) instead of neutral
  227 because the Prism appearance has a second colour property
  (`surface_albedo`) and the shaded style darkens it slightly; the masked
  face rendered (70, 68, 64). Fix: all albedo colour properties are now set,
  and every capture is converted to neutral gray with the dominant light tone
  mapped to exactly 227 and the masked tone to 64 (`NORMALIZE_TONES`).
- **Pivot marker.** A small green, semi-transparent dot sits at the capture
  centre (the camera target) in the side and dodge captures. The camera pivot
  preference is switched off during rendering and restored, and green
  semi-transparent pixels near the centre are removed as a safety net.
- **Resolution.** With the 3420 × 1277 viewport the fit is governed by
  height, so the ring only spanned 870 of 2400 px. Captures are now widened
  (up to `MAX_CAPTURE_PX`) so the part spans about `RENDER_PX` pixels.
- **In-place fallback** failed on a wrong namespace
  (`CustomGraphicsSolidColorEffect` lives in `adsk.fusion`); fixed, untested,
  and not needed.
- **Python 3.14.0** is already what this Fusion build embeds; the code targets
  3.12 and 3.14.

The captures are kept in `reference/spike-2026-10-06/`.
