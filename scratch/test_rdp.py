from shapely.geometry import LineString

geom = LineString([(0.0, 0.0), (1.0, 0.0), (2.0, 0.1), (3.0, 0.0), (4.0, 0.0)])
simp = geom.simplify(0.00005, preserve_topology=False)
print("Points:", list(simp.coords))
