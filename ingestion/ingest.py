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


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_campaign(lsf_path):
    """Extract campaign name from folder path.

    The mission folder (direct parent of Data.lsf) is typically named
    HHMMSS_campaignname — strip the time prefix to get the campaign.
    """
    mission_folder = os.path.basename(os.path.dirname(lsf_path))
    m = re.match(r"^\d{6}_(.+)$", mission_folder)
    return m.group(1) if m else mission_folder


def read_msgs(path):
    """Read all relevant IMC messages from .lsf or .lsf.gz in one pass."""
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
    """Return vehicle sys_name from the Announce message whose src matches
    the EstimatedState source (i.e. the vehicle itself, not the CCU)."""
    es_msgs = buckets[imcpy.EstimatedState]
    if not es_msgs:
        return None
    auv_src = es_msgs[0].src
    for msg in buckets[imcpy.Announce]:
        if msg.src == auv_src:
            return msg.sys_name
    # Fallback: first UUV/AUV announce
    for msg in buckets[imcpy.Announce]:
        if msg.sys_type.name in ("UUV", "AUV", "USV"):
            return msg.sys_name
    return None


def align_sensor(es_msgs, sensor_msgs):
    """Return list of nearest sensor values aligned to es_msgs using two-pointer O(n+m)."""
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
    buckets = read_msgs(lsf_path)

    es_msgs = buckets[imcpy.EstimatedState]
    if not es_msgs:
        return []

    # Year and date from first message timestamp
    first_ts = datetime.fromtimestamp(es_msgs[0].timestamp, tz=timezone.utc)
    year = first_ts.year

    vehicle = get_vehicle_name(buckets) or "AUV"

    sal_vals  = align_sensor(es_msgs, buckets[imcpy.Salinity])
    temp_vals = align_sensor(es_msgs, buckets[imcpy.Temperature])
    cond_vals = align_sensor(es_msgs, buckets[imcpy.Conductivity])

    name = os.path.basename(os.path.dirname(lsf_path))
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
            "geom":         Point(lon, lat, msg.depth),
        })
    return points


# --- Database setup ---
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
]:
    cur.execute(f"ALTER TABLE auv_tracks ADD COLUMN IF NOT EXISTS {col} {typ}")
conn.commit()

cur.execute("TRUNCATE TABLE auv_tracks RESTART IDENTITY")
conn.commit()

# --- Find files: prefer .lsf over .lsf.gz when both exist ---
all_lsf = set(glob.glob("/data/**/*.lsf", recursive=True))
all_gz  = set(glob.glob("/data/**/*.lsf.gz", recursive=True))

lsf_files = list(all_lsf)
for gz in all_gz:
    if gz[:-3] not in all_lsf:
        lsf_files.append(gz)

lsf_files.sort()

# Dedupliser på filinnhold (MD5) — samme logg kan ligge i både kortnavns- og beskrivelsesmapper
seen_hashes = set()
unique_files = []
for path in lsf_files:
    h = file_md5(path)
    if h in seen_hashes:
        print(f"Hopper over duplikat: {os.path.dirname(path)}")
    else:
        seen_hashes.add(h)
        unique_files.append(path)
lsf_files = unique_files

print(f"Fant {len(lsf_files)} unike LSF-filer")

total = 0
for path in lsf_files:
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
                salinity, temperature, conductivity, geom)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_GeomFromWKB(%s::geometry,4326))""",
            (p["name"], p["vehicle"], p["timestamp"], p["depth"],
             p["year"], p["campaign"],
             p["salinity"], p["temperature"], p["conductivity"],
             dumps(p["geom"], hex=True)),
        )
    conn.commit()
    total += len(points)
    sal_str = f"{p0['salinity']:.2f}" if p0['salinity'] is not None else "N/A"
    print(f"  → {len(points)} punkter | farkost={p0['vehicle']!r} "
          f"år={p0['year']} kampanje={p0['campaign']!r} sal={sal_str}")

print(f"\nFerdig! Totalt {total} punkter i PostGIS")
cur.close()
conn.close()
