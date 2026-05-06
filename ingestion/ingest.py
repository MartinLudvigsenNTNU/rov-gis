import imcpy
import imcpy.lsf as lsf
from datetime import datetime
import math
import glob
import os
import psycopg2
from shapely.geometry import Point
from shapely.wkb import dumps

def lsf_to_points(lsf_path):
    with lsf.LSFReader(lsf_path, save_index=False) as reader:
        msgs = list(reader.read_message(types=[imcpy.EstimatedState]))
    points = []
    name = os.path.basename(os.path.dirname(lsf_path))
    for msg in msgs:
        lat = math.degrees(msg.lat) + (msg.x / 111320)
        lon = math.degrees(msg.lon) + (msg.y / (111320 * math.cos(msg.lat)))
        points.append({
            "name": name,
            "vehicle": "AUV",
            "timestamp": datetime.utcfromtimestamp(msg.timestamp),
            "depth": msg.depth,
            "geom": Point(lon, lat, msg.depth)
        })
    return points

# Koble til PostGIS
conn = psycopg2.connect(
    host="postgis",
    database="rovdb",
    user="rovadmin",
    password="rovpassword"
)
cur = conn.cursor()

# Finn og last alle LSF-filer
lsf_files = glob.glob('/data/**/*.lsf', recursive=True)
print(f"Fant {len(lsf_files)} LSF-filer")

total = 0
for path in sorted(lsf_files):
    name = os.path.basename(os.path.dirname(path))
    print(f"Laster {name}...")
    points = lsf_to_points(path)
    for p in points:
        cur.execute(
            "INSERT INTO auv_tracks (name, vehicle, timestamp, depth, geom) "
            "VALUES (%s, %s, %s, %s, ST_GeomFromWKB(%s::geometry, 4326))",
            (p["name"], p["vehicle"], p["timestamp"], p["depth"],
             dumps(p["geom"], hex=True))
        )
    conn.commit()
    total += len(points)
    print(f"  → {len(points)} punkter lastet")

print(f"\nFerdig! Totalt {total} punkter i PostGIS")
cur.close()
conn.close()
