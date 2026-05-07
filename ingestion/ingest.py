import imcpy
import imcpy.lsf as lsf
from datetime import datetime, timezone
import hashlib
import math
import glob
import gzip
import os
import re
import shutil
import tempfile
import psycopg2
from shapely.geometry import Point
from shapely.wkb import dumps

DATA_ROOT = "/data"


def rel_path(abs_path):
    """Relativ sti fra DATA_ROOT — brukes som source_path i DB."""
    return os.path.relpath(abs_path, DATA_ROOT)


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_campaign(lsf_path):
    mission_folder = os.path.basename(os.path.dirname(lsf_path))
    m = re.match(r"^\d{6}_(.+)$", mission_folder)
    return m.group(1) if m else mission_folder


def read_msgs(path):
    types = [
        imcpy.EstimatedState,
        imcpy.Announce,
        imcpy.Salinity,
        imcpy.Temperature,
        imcpy.Conductivity,
    ]
    if path.endswith(".gz"):
        with tempfile.NamedTemporaryFile(suffix=".lsf", delete=False) as tmp:
            with gzip.open(path, "rb") as gz:
                shutil.copyfileobj(gz, tmp)
            tmp_path = tmp.name
        try:
            return _read_from_path(tmp_path, types)
        finally:
            os.unlink(tmp_path)
    else:
        return _read_from_path(path, types)


def _read_from_path(path, types):
    buckets = {t: [] for t in types}
    with lsf.LSFReader(path, save_index=False) as reader:
        for msg in reader.read_message(types=types):
            buckets[type(msg)].append(msg)
    return buckets


def get_vehicle_name(buckets):
    es_msgs = buckets[imcpy.EstimatedState]
    if not es_msgs:
        return None
    auv_src = es_msgs[0].src
    for msg in buckets[imcpy.Announce]:
        if msg.src == auv_src:
            return msg.sys_name
    for msg in buckets[imcpy.Announce]:
        if msg.sys_type.name in ("UUV", "AUV", "USV"):
            return msg.sys_name
    return None


def align_sensor(es_msgs, sensor_msgs):
    """Nærmeste sensorverdi per EstimatedState-punkt, O(n+m) to-peker."""
    if not sensor_msgs:
        return [None] * len(es_msgs)
    result = []
    j = 0
    n = len(sensor_msgs)
    for msg in es_msgs:
        ts = msg.timestamp
        while j < n - 1 and abs(sensor_msgs[j + 1].timestamp - ts) <= abs(sensor_msgs[j].timestamp - ts):
            j += 1
        result.append(sensor_msgs[j].value)
    return result


def lsf_to_points(lsf_path):
    campaign = extract_campaign(lsf_path)
    buckets  = read_msgs(lsf_path)
    es_msgs  = buckets[imcpy.EstimatedState]
    if not es_msgs:
        return []

    first_ts = datetime.fromtimestamp(es_msgs[0].timestamp, tz=timezone.utc)
    year     = first_ts.year
    vehicle  = (get_vehicle_name(buckets) or "auv").lower()

    sal_vals  = align_sensor(es_msgs, buckets[imcpy.Salinity])
    temp_vals = align_sensor(es_msgs, buckets[imcpy.Temperature])
    cond_vals = align_sensor(es_msgs, buckets[imcpy.Conductivity])

    name   = os.path.basename(os.path.dirname(lsf_path))
    src    = rel_path(lsf_path)
    points = []
    for i, msg in enumerate(es_msgs):
        lat = math.degrees(msg.lat) + (msg.x / 111320)
        lon = math.degrees(msg.lon) + (msg.y / (111320 * math.cos(msg.lat)))
        points.append({
            "name":         name,
            "vehicle":      vehicle,
            "timestamp":    datetime.utcfromtimestamp(msg.timestamp),
            "depth":        msg.depth,
            "year":         year,
            "campaign":     campaign,
            "salinity":     sal_vals[i],
            "temperature":  temp_vals[i],
            "conductivity": cond_vals[i],
            "source_path":  src,
            "geom":         Point(lon, lat, msg.depth),
        })
    return points


# --- Database ---
conn = psycopg2.connect(
    host="postgis", database="rovdb", user="rovadmin", password="rovpassword"
)
cur = conn.cursor()

for col, typ in [
    ("year",         "INTEGER"),
    ("campaign",     "VARCHAR(255)"),
    ("salinity",     "FLOAT"),
    ("temperature",  "FLOAT"),
    ("conductivity", "FLOAT"),
    ("source_path",  "VARCHAR(500)"),
]:
    cur.execute(f"ALTER TABLE auv_tracks ADD COLUMN IF NOT EXISTS {col} {typ}")
conn.commit()

# --- Finn filer: foretrekk .lsf over .lsf.gz når begge finnes ---
all_lsf = set(glob.glob(f"{DATA_ROOT}/**/*.lsf",    recursive=True))
all_gz  = set(glob.glob(f"{DATA_ROOT}/**/*.lsf.gz", recursive=True))

lsf_files = list(all_lsf)
for gz in all_gz:
    if gz[:-3] not in all_lsf:
        lsf_files.append(gz)

lsf_files.sort()

# Dedupliser på MD5 (samme logg i ulike mapper)
seen_hashes = set()
unique_files = []
for path in lsf_files:
    h = file_md5(path)
    if h in seen_hashes:
        print(f"Hopper over duplikat: {os.path.dirname(path)}")
    else:
        seen_hashes.add(h)
        unique_files.append(path)

# Backfill source_path for eksisterende rader som mangler den
cur.execute("SELECT DISTINCT name FROM auv_tracks WHERE source_path IS NULL")
names_missing = {row[0] for row in cur.fetchall()}
if names_missing:
    name_to_path = {os.path.basename(os.path.dirname(p)): rel_path(p) for p in unique_files}
    updated = 0
    for name in names_missing:
        if name in name_to_path:
            cur.execute(
                "UPDATE auv_tracks SET source_path = %s WHERE name = %s AND source_path IS NULL",
                (name_to_path[name], name)
            )
            updated += 1
    conn.commit()
    print(f"Backfill: source_path satt på {updated} eksisterende kampanjer")

# Hent allerede ingestede source_path-er
cur.execute("SELECT DISTINCT source_path FROM auv_tracks WHERE source_path IS NOT NULL")
ingested = {row[0] for row in cur.fetchall()}

# Filtrer bort allerede ingestede filer
new_files = [p for p in unique_files if rel_path(p) not in ingested]
print(f"Fant {len(unique_files)} unike LSF-filer, {len(new_files)} nye ({len(ingested)} allerede i DB)")

total = 0
for path in new_files:
    ext  = ".lsf.gz" if path.endswith(".gz") else ".lsf"
    name = os.path.basename(os.path.dirname(path))
    print(f"Laster {name} ({ext})...")

    points = lsf_to_points(path)
    if not points:
        print("  → ingen EstimatedState-meldinger, hopper over")
        continue

    p0 = points[0]
    for p in points:
        cur.execute(
            """INSERT INTO auv_tracks
               (name, vehicle, timestamp, depth, year, campaign,
                salinity, temperature, conductivity, source_path, geom)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_GeomFromWKB(%s::geometry,4326))""",
            (p["name"], p["vehicle"], p["timestamp"], p["depth"],
             p["year"], p["campaign"],
             p["salinity"], p["temperature"], p["conductivity"],
             p["source_path"], dumps(p["geom"], hex=True)),
        )
    conn.commit()
    total += len(points)
    sal_str = f"{p0['salinity']:.2f}" if p0["salinity"] is not None else "N/A"
    print(f"  → {len(points)} punkter | farkost={p0['vehicle']!r} "
          f"år={p0['year']} kampanje={p0['campaign']!r} sal={sal_str}")

print(f"\nFerdig! {total} nye punkter lagt til ({total + sum(1 for _ in ingested)} totalt)")
cur.close()
conn.close()
