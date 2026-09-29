# Integrated base building artwork

Generated with the built-in image_gen tool on 2026-09-24. Original assets are retained.

Each PNG in this directory is a transparent replacement for the matching standard `../building_{key}.png`: command_center, warehouse, embassy, barracks, research_center, deathstill, influence_sanctuary. Higher-level artwork variants retain their existing selection.

References: the original matching building and `../base_courtyard_background_v3.png` for camera, material and lighting.

Prompt used for each building (substitute its key):

Edit target image 1: command_center building. Image 2 is ONLY the lighting, camera and material reference for the courtyard where this sprite will be composited. Produce a seamless placeable building sprite, NOT a round token. Preserve the recognizable core architecture and purpose of image 1, domes, doors, functional equipment. REMOVE the outer circular perimeter wall, round pedestal, circular rim and broad disk of paving entirely. Keep only the actual building and directly attached functional annexes; no enclosing wall. Building sits directly on sand with tiny irregular sandy contact edges; no broad terrain patch and no white halo. Match image 2's warm ochre sandstone, soft morning light from upper left, shadows toward lower right, isometric elevated camera. Avoid bright gold highlights and excessive contrast. Entire building centered in square image with 8% transparent padding, feet/base of building near 88% image height for consistent placement. Real alpha transparency everywhere outside structure and a very subtle small contact shadow; remove all checkerboard and white background. No backdrop, no UI, no words, no emblem glow, no border. Detailed painterly 3D game style; legible at 150px. Do not incorporate any background buildings from reference 2.

Integration: dashboard map and detail previews select these standard sprites. Built structures sit on the courtyard without the original circular perimeter walls or platforms. Unbuilt plots show the building preview with a Not built label; construction state remains unchanged. Map CSS removes glow filters and anchors the artwork near its ground contact point.

