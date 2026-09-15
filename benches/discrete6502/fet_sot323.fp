# True-size footprints for the discrete6502 bench (SOT-323/0402 density).
# ocd's stdlib SOT23/R0402 carry courtyard margins that false-overlap at
# 3.7x2.8mm die-true pitch. Dimensions in mm, pads (dx dy w h).
footprint FET_SOT323 2.0x1.25
pad 1 -0.65 -0.65 0.4 0.5
pad 2 -0.65 0.65 0.4 0.5
pad 3 0.65 0 0.4 0.5
body box 1.0 0.6 0.55
