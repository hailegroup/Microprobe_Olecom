import numpy as np
import ezdxf

# Units in µm
width = 8_000 # Width of gradient thin <-> thick
length = 4_000 # Length is perpendicular to gradient
edge_gap = 200

# Number of columns is constrained by defined electrode sizes
radii = [75, 75, 125, 125, 175, 175, 225, 225]
n_radii = len(radii)
min_electrode_gap = 150

class Circle:
    def __init__(self, x=None, y=None, r=None):
        self.x = x
        self.y = y
        self.r = r

circles = [Circle(r=r) for r in radii]
inverted_circles = [Circle(r=r) for r in radii]

# Calculate vertical gap between electrodes
remaining_length = length - 2*edge_gap - 2*sum(radii)
vertical_gap = remaining_length / (n_radii - 1)
if vertical_gap < min_electrode_gap:
    print(f"""Electrodes do not fit within {length} length.
            Remaining length: {remaining_length} µm
            Distance from edges: {edge_gap} µm
            Distance between electrodes: {min_electrode_gap} µm""")

# Calculate electrode y
prev_y = 0
for i in range(n_radii):
    if i == 0:
        y = edge_gap + radii[i]
    else:
        y = prev_y + radii[i-1] + vertical_gap + radii[i]
    circles[i].y = y
    inverted_circles[i].y = length - y
    prev_y = y

# Calculate minimum horizontal distance between columns of electrodes
min_horizontal_distance = 0
for i in range(n_radii):
    for j in range(i, n_radii):
        c1, c2 = circles[i], inverted_circles[j]
        discriminant = (min_electrode_gap + c1.r + c2.r)**2 - (c1.y - c2.y)**2
        required_distance = np.sqrt(max(0.0, discriminant))
        if required_distance > min_horizontal_distance:
            min_horizontal_distance = required_distance
            r_pair = (c1.r, c2.r)

# Space columns of electrodes so they occupy the entire width of the sample (minus edge gaps)
available_width = width - 2*edge_gap - 2*max(radii)
n_columns = int(available_width // min_horizontal_distance) + 1
horizontal_distance = available_width/(n_columns - 1) if n_columns > 1 else 0.0

# Calculate electrode x and create electrode array
electrodes = []
inverted = False
for i in range(n_columns):
    source = inverted_circles if inverted else circles
    x = edge_gap + max(radii) + i*horizontal_distance
    for c in source:
        electrodes.append(Circle(x=x, y=c.y, r=c.r))
    inverted = not inverted

# Write to dxf file
dxf_doc = ezdxf.new()
dxf_msp = dxf_doc.modelspace()
for e in electrodes:
    dxf_msp.add_circle((e.x/1000, e.y/1000), e.r/1000) # DXF written in mm
dxf_doc.saveas('vision_calibration/electrodes.dxf')
print('Wrote to electrodes.dxf')
