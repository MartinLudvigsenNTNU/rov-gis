import imcpy
import imcpy.lsf as lsf
from datetime import datetime, timezone
import base64
import hashlib
import math
import glob
import gzip
import os
import re
import shutil
import struct
import tempfile
import urllib.request
import urllib.error
import psycopg2
from shapely.geometry import Point
from shapely.wkb import dumps

DATA_ROOT    = "/data"
GEOSERVER    = "http://geoserver:8080/geoserver"
_GS_USER     = os.environ.get("GEOSERVER_ADMIN_USER", "admin")
_GS_PASS     = os.environ.get("GEOSERVER_ADMIN_PASSWORD", "geoserver")
GWC_CREDS    = base64.b64encode(f"{_GS_USER}:{_GS_PASS}".encode()).decode()

_VEHICLE_LAYER = {
    "lauv-fridtjof": "NTNU:auv_fridtjof",
    "lauv-harald":   "NTNU:auv_harald",
    "lauv-roald":    "NTNU:auv_roald",
    "lauv-thor":     "NTNU:auv_thor",
    "lauv-marie":    "NTNU:auv_marie",
}


def gwc_truncate(vehicle, year):
    """Invalider cachede tiles for eit lag+år-par etter ny ingestion."""
    layer = _VEHICLE_LAYER.get(vehicle)
    if not layer:
        return
    for gridset in ("EPSG:900913", "EPSG:4326"):
        xml = (
            f"<seedRequest>"
            f"<name>{layer}</name>"
            f"<gridSetId>{gridset}</gridSetId>"
            f"<zoomStart>0</zoomStart><zoomStop>20</zoomStop>"
            f"<type>truncate</type><threadCount>1</threadCount>"
            f"<parameters><entry>"
            f"<string>CQL_FILTER</string><string>year={year}</string>"
            f"</entry></parameters>"
            f"</seedRequest>"
        )
        url = f"{GEOSERVER}/gwc/rest/seed/{layer}"
        req = urllib.request.Request(
            url, data=xml.encode(), method="POST",
            headers={
                "Authorization": f"Basic {GWC_CREDS}",
                "Content-Type": "application/xml",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                pass  # 200 OK — truncate startet
        except Exception as e:
            print(f"  ⚠ GWC truncate feilet ({gridset}): {e}")


def rel_path(abs_path):
    """Relativ sti fra DATA_ROOT — brukes som source_path i DB."""
    return os.path.relpath(abs_path, DATA_ROOT)


def file_md5(path):
    """MD5 av dekomprimert innhold — .lsf og .lsf.gz med samme data gir samme hash."""
    h = hashlib.md5()
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_campaign(lsf_path):
    mission_folder = os.path.basename(os.path.dirname(lsf_path))
    m = re.match(r"^\d{6}_(.+)$", mission_folder)
    return m.group(1) if m else mission_folder


# IMC header: sync(2) mgid(2) size(2) timestamp(8) src(2) src_ent(1) dst(2) dst_ent(1)
_HDR = struct.Struct('<HHHdHBHB')
_HDR_SIZE = 20
_SYNC_IMC5 = 0xFE54
_SYNC_IMC6 = 0xFE55  # IMC v5.5 — header format identical, only sync differs

# Payload layouts for messages we need
_ES  = struct.Struct('<ddffffffffffffffffff')   # EstimatedState: lat lon height x y z phi theta psi u v w vx vy vz p q r depth alt
_SAL = struct.Struct('<f')   # Salinity: value
_TMP = struct.Struct('<f')   # Temperature: value
_CND = struct.Struct('<f')   # Conductivity: value

_MGID_ES   = 350
_MGID_ANN  = 151
_MGID_SAL  = 270
_MGID_TMP  = 263
_MGID_CND  = 269


class _Msg:
    """Enkel meldings-container brukt av den raw parseren."""
    __slots__ = ('timestamp', 'src', 'lat', 'lon', 'x', 'y', 'depth', 'speed', 'value', 'sys_name', 'sys_type_name')


def _decode_imc_string(data, offset):
    """IMC plaintext: uint16 lengde + UTF-8 bytes."""
    length = struct.unpack_from('<H', data, offset)[0]
    return data[offset+2: offset+2+length].decode('utf-8', errors='replace'), offset + 2 + length


def _read_raw(data):
    """Parser for IMC5 (0xFE54) og IMC5.5 (0xFE55) — hopper over CRC-validering."""
    buckets = {'es': [], 'ann': [], 'sal': [], 'tmp': [], 'cnd': []}
    offset = 0
    n = len(data)
    while offset + _HDR_SIZE <= n:
        sync, mgid, size, ts, src, _, _, _ = _HDR.unpack_from(data, offset)
        if sync not in (_SYNC_IMC5, _SYNC_IMC6):
            break
        end = offset + _HDR_SIZE + size
        if end + 2 > n:
            break
        payload = data[offset + _HDR_SIZE: end]

        if mgid == _MGID_ES and len(payload) >= _ES.size:
            vals = _ES.unpack_from(payload)
            m = _Msg()
            m.timestamp, m.src = ts, src
            m.lat, m.lon = vals[0], vals[1]
            m.x,   m.y   = vals[3], vals[4]
            m.depth       = vals[18]
            u, v, w       = vals[9], vals[10], vals[11]
            m.speed       = math.sqrt(u*u + v*v + w*w)
            buckets['es'].append(m)

        elif mgid == _MGID_ANN and len(payload) >= 3:
            try:
                sys_name, off2 = _decode_imc_string(payload, 0)
                sys_type_raw = payload[off2]
                m = _Msg()
                m.timestamp, m.src = ts, src
                m.sys_name = sys_name
                # sys_type enum: 0=CCU,1=HMANU,2=MOBILESENSOR,3=STATICSENSOR,4=UUV,5=USV,6=UAV,7=UGV,8=SUBMARINE
                m.sys_type_name = {4: 'UUV', 5: 'USV', 6: 'UAV'}.get(sys_type_raw, 'OTHER')
                buckets['ann'].append(m)
            except Exception:
                pass

        elif mgid == _MGID_SAL and len(payload) >= 4:
            m = _Msg(); m.timestamp = ts; m.value = _SAL.unpack_from(payload)[0]
            buckets['sal'].append(m)
        elif mgid == _MGID_TMP and len(payload) >= 4:
            m = _Msg(); m.timestamp = ts; m.value = _TMP.unpack_from(payload)[0]
            buckets['tmp'].append(m)
        elif mgid == _MGID_CND and len(payload) >= 4:
            m = _Msg(); m.timestamp = ts; m.value = _CND.unpack_from(payload)[0]
            buckets['cnd'].append(m)

        offset = end + 2  # skip 2-byte CRC footer
    return buckets


def _peek_sync(path):
    """Les de to første bytene for å sjekke sync-nummer."""
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rb') as f:
        two = f.read(2)
    if len(two) < 2:
        return None
    return struct.unpack_from('<H', two)[0]


def _load_bytes(path):
    if path.endswith('.gz'):
        with gzip.open(path, 'rb') as f:
            return f.read()
    with open(path, 'rb') as f:
        return f.read()


def read_msgs(path):
    sync = _peek_sync(path)
    if sync == _SYNC_IMC6:
        raw = _read_raw(_load_bytes(path))
        # Konverter til samme struktur som imcpy-stien bruker
        return {'imc6': True, 'raw': raw}

    # IMC5 — bruk imcpy som før
    types = [imcpy.EstimatedState, imcpy.Announce, imcpy.Salinity, imcpy.Temperature, imcpy.Conductivity]
    if path.endswith('.gz'):
        with tempfile.NamedTemporaryFile(suffix='.lsf', delete=False) as tmp:
            with gzip.open(path, 'rb') as gz:
                shutil.copyfileobj(gz, tmp)
            tmp_path = tmp.name
        try:
            return _read_from_path(tmp_path, types)
        finally:
            os.unlink(tmp_path)
    return _read_from_path(path, types)


def _read_from_path(path, types):
    buckets = {t: [] for t in types}
    with lsf.LSFReader(path, save_index=False) as reader:
        for msg in reader.read_message(types=types):
            buckets[type(msg)].append(msg)
    return buckets


def get_vehicle_name(buckets):
    if buckets.get('imc6'):
        raw = buckets['raw']
        es_list = raw['es']
        if not es_list:
            return None
        auv_src = es_list[0].src
        for ann in raw['ann']:
            if ann.src == auv_src:
                return ann.sys_name
        for ann in raw['ann']:
            if ann.sys_type_name in ('UUV', 'USV'):
                return ann.sys_name
        return None
    es_msgs = buckets[imcpy.EstimatedState]
    if not es_msgs:
        return None
    auv_src = es_msgs[0].src
    for msg in buckets[imcpy.Announce]:
        if msg.src == auv_src:
            return msg.sys_name
    for msg in buckets[imcpy.Announce]:
        if msg.sys_type.name in ('UUV', 'AUV', 'USV'):
            return msg.sys_name
    return None


def align_sensor(es_msgs, sensor_msgs, use_raw=False):
    """Nærmeste sensorverdi per ES-punkt, O(n+m) to-peker."""
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

    if buckets.get('imc6'):
        raw     = buckets['raw']
        es_msgs = raw['es']
        if not es_msgs:
            return []
        first_ts = datetime.fromtimestamp(es_msgs[0].timestamp, tz=timezone.utc)
        year     = first_ts.year
        vehicle  = (get_vehicle_name(buckets) or "auv").lower()
        sal_vals  = align_sensor(es_msgs, raw['sal'])
        temp_vals = align_sensor(es_msgs, raw['tmp'])
        cond_vals = align_sensor(es_msgs, raw['cnd'])
        name = os.path.basename(os.path.dirname(lsf_path))
        src  = rel_path(lsf_path)
        points = []
        for i, msg in enumerate(es_msgs):
            lat = math.degrees(msg.lat) + (msg.x / 111320)
            lon = math.degrees(msg.lon) + (msg.y / (111320 * math.cos(msg.lat)))
            points.append({
                "name":           name,
                "vehicle":        vehicle,
                "timestamp":      datetime.utcfromtimestamp(msg.timestamp),
                "depth":          msg.depth,
                "year":           year,
                "campaign":       campaign,
                "salinity":       sal_vals[i],
                "temperature":    temp_vals[i],
                "conductivity":   cond_vals[i],
                "source_path":    src,
                "speed":          msg.speed,
                "vessel_transit": msg.speed > 2.5,
                "geom":           Point(lon, lat, msg.depth),
            })
        return points

    # IMC5 — imcpy-path
    es_msgs = buckets[imcpy.EstimatedState]
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
        spd = math.sqrt(msg.u**2 + msg.v**2 + msg.w**2)
        points.append({
            "name":           name,
            "vehicle":        vehicle,
            "timestamp":      datetime.utcfromtimestamp(msg.timestamp),
            "depth":          msg.depth,
            "year":           year,
            "campaign":       campaign,
            "salinity":       sal_vals[i],
            "temperature":    temp_vals[i],
            "conductivity":   cond_vals[i],
            "source_path":    src,
            "speed":          spd,
            "vessel_transit": spd > 2.5,
            "geom":           Point(lon, lat, msg.depth),
        })
    return points


# --- Database ---
conn = psycopg2.connect(
    host=os.environ.get("POSTGRES_HOST", "postgis"),
    database=os.environ.get("POSTGRES_DB", "rovdb"),
    user=os.environ.get("POSTGRES_USER", "rovadmin"),
    password=os.environ.get("POSTGRES_PASSWORD"),
)
cur = conn.cursor()

for col, typ in [
    ("year",         "INTEGER"),
    ("campaign",     "VARCHAR(255)"),
    ("salinity",     "FLOAT"),
    ("temperature",  "FLOAT"),
    ("conductivity",   "FLOAT"),
    ("source_path",    "VARCHAR(500)"),
    ("speed",          "FLOAT"),
    ("vessel_transit", "BOOLEAN"),
]:
    cur.execute(f"ALTER TABLE auv_tracks ADD COLUMN IF NOT EXISTS {col} {typ}")
conn.commit()

# --- Finn filer: inkluder både .lsf og .lsf.gz; MD5-dedup håndterer ekte duplikater ---
all_lsf = set(glob.glob(f"{DATA_ROOT}/**/*.lsf",    recursive=True))
all_gz  = set(glob.glob(f"{DATA_ROOT}/**/*.lsf.gz", recursive=True))

lsf_files = sorted(all_lsf | all_gz)

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
                salinity, temperature, conductivity, source_path,
                speed, vessel_transit, geom)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_GeomFromWKB(%s::geometry,4326))""",
            (p["name"], p["vehicle"], p["timestamp"], p["depth"],
             p["year"], p["campaign"],
             p["salinity"], p["temperature"], p["conductivity"],
             p["source_path"], p["speed"], p["vessel_transit"],
             dumps(p["geom"], hex=True)),
        )
    conn.commit()
    gwc_truncate(p0["vehicle"], p0["year"])
    total += len(points)
    sal_str = f"{p0['salinity']:.2f}" if p0["salinity"] is not None else "N/A"
    print(f"  → {len(points)} punkter | farkost={p0['vehicle']!r} "
          f"år={p0['year']} kampanje={p0['campaign']!r} sal={sal_str}")

print(f"\nFerdig! {total} nye punkter lagt til ({total + sum(1 for _ in ingested)} totalt)")

if total > 0:
    print("Refresher auv_missions…")
    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY auv_missions")
    conn.commit()
    print("auv_missions oppdatert.")

cur.close()
conn.close()
