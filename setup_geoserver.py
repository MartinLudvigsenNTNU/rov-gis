#!/usr/bin/env python3
"""One-shot GeoServer setup for rov-gis.

Creates workspace NTNU, PostGIS datastore rovdb, SLD styles, and
publishes all layers from existing PostGIS views and materialized views.
"""
import sys, time
import requests

GS   = "http://localhost:8080/geoserver/rest"
AUTH = ("admin", "geoserver")

# Vehicle WMS layers: (view_name, vehicle_label, hex_color)
VEHICLE_LAYERS = [
    ("auv_fridtjof", "lauv-fridtjof tracks",  "2277cc"),
    ("auv_harald",   "lauv-harald tracks",    "cc3300"),
    ("auv_roald",    "lauv-roald tracks",      "00cc66"),
    ("auv_thor",     "lauv-thor tracks",       "aa44ff"),
    ("auv_marie",    "lauv-marie tracks",      "00cccc"),
]

# WFS / dashboard layers: (view_name, title)
WFS_LAYERS = [
    ("auv_missions",               "AUV Missions"),
    ("auv_missions_all",           "AUV Missions (all)"),
    ("dive_time_wfs",              "Dive time by vehicle"),
    ("dive_time_mat",              "Dive time (materialized)"),
    ("mission_hours_wfs",          "Mission hours by vehicle"),
    ("mission_hours_mat",          "Mission hours (materialized)"),
    ("mission_status_wfs",         "Mission status"),
    ("mission_status_mat",         "Mission status (materialized)"),
    ("mission_time_wfs",           "Mission time"),
    ("track_km_wfs",               "Track km by vehicle/year"),
    ("track_km_all_wfs",           "Track km all vehicles"),
    ("track_km_by_vehicle_year",   "Track km by vehicle/year (mat)"),
    ("track_km_all_by_vehicle_year","Track km all by vehicle/year (mat)"),
]


def req(method, path, ok=(200, 201, 409), **kw):
    url = f"{GS}/{path}"
    r = getattr(requests, method)(url, auth=AUTH, **kw)
    flag = "" if r.status_code in ok else f"  ← {r.text[:180]}"
    print(f"  {method.upper():4} /{path} → {r.status_code}{flag}")
    return r


def wait_for_geoserver():
    print("Waiting for GeoServer to be ready...")
    for _ in range(72):
        try:
            if requests.get(f"{GS}/about/version.json", auth=AUTH, timeout=5).status_code == 200:
                print("GeoServer ready.\n")
                return
        except Exception:
            pass
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(5)
    sys.exit("\nGeoServer did not start within 6 minutes")


def make_sld(name, color):
    # Filter: only render points where vessel_transit = false (or null).
    # Points flagged vessel_transit=true are support-vessel transit (speed > 2.5 m/s)
    # and must be excluded from all track layers.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0"
  xmlns="http://www.opengis.net/sld"
  xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:xlink="http://www.w3.org/1999/xlink">
  <NamedLayer><Name>{name}</Name>
    <UserStyle><Title>{name}</Title>
      <FeatureTypeStyle><Rule>
        <ogc:Filter>
          <ogc:PropertyIsNotEqualTo>
            <ogc:PropertyName>vessel_transit</ogc:PropertyName>
            <ogc:Literal>true</ogc:Literal>
          </ogc:PropertyIsNotEqualTo>
        </ogc:Filter>
        <PointSymbolizer>
          <Graphic><Mark>
            <WellKnownName>circle</WellKnownName>
            <Fill><CssParameter name="fill">#{color}</CssParameter></Fill>
            <Stroke>
              <CssParameter name="stroke">#ffffff</CssParameter>
              <CssParameter name="stroke-width">0.3</CssParameter>
            </Stroke>
          </Mark><Size>4</Size></Graphic>
        </PointSymbolizer>
      </Rule></FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>"""


def publish_featuretype(name, title, srs="EPSG:4326"):
    return req("post", "workspaces/NTNU/datastores/rovdb/featuretypes",
               headers={"Content-Type": "application/json"},
               json={
                   "featureType": {
                       "name": name,
                       "nativeName": name,
                       "title": title,
                       "srs": srs,
                       "projectionPolicy": "FORCE_DECLARED",
                   }
               })


def main():
    wait_for_geoserver()

    # 1. Workspace
    print("=== Workspace ===")
    req("post", "workspaces",
        headers={"Content-Type": "application/json"},
        json={"workspace": {"name": "NTNU"}})

    # 2. PostGIS datastore (hostname = docker service name)
    print("\n=== Datastore ===")
    req("post", "workspaces/NTNU/datastores",
        headers={"Content-Type": "application/json"},
        json={
            "dataStore": {
                "name": "rovdb",
                "type": "PostGIS",
                "connectionParameters": {"entry": [
                    {"@key": "host",                "$": "postgis"},
                    {"@key": "port",                "$": "5432"},
                    {"@key": "database",            "$": "rovdb"},
                    {"@key": "user",                "$": "rovadmin"},
                    {"@key": "passwd",              "$": "rovpassword"},
                    {"@key": "dbtype",              "$": "postgis"},
                    {"@key": "schema",              "$": "public"},
                    {"@key": "Expose primary keys", "$": "true"},
                    {"@key": "preparedStatements",  "$": "true"},
                    {"@key": "fetchSize",           "$": "1000"},
                ]}
            }
        })

    # 3. SLD styles (one per vehicle)
    print("\n=== Styles ===")
    for name, _, color in VEHICLE_LAYERS:
        req("post", "styles",
            headers={"Content-Type": "application/json"},
            json={"style": {"name": name, "filename": f"{name}.sld"}})
        r = requests.put(
            f"{GS}/styles/{name}",
            auth=AUTH,
            headers={"Content-Type": "application/vnd.ogc.sld+xml"},
            data=make_sld(name, color).encode(),
        )
        print(f"  PUT  /styles/{name} (SLD upload) → {r.status_code}")

    # 4. Vehicle WMS layers + assign style
    print("\n=== Vehicle WMS layers ===")
    for name, title, _ in VEHICLE_LAYERS:
        r = publish_featuretype(name, title)
        if r.status_code in (200, 201, 409):
            req("put", f"layers/NTNU:{name}",
                headers={"Content-Type": "application/json"},
                json={"layer": {"defaultStyle": {"name": name}}})

    # 5. WFS / dashboard layers
    print("\n=== WFS / dashboard layers ===")
    for name, title in WFS_LAYERS:
        publish_featuretype(name, title)

    # 6. Publish raw auv_tracks table (useful for debugging / future use)
    print("\n=== auv_tracks table ===")
    publish_featuretype("auv_tracks", "AUV tracks (all vehicles)")

    # 7. Verify
    print("\n=== Verification ===")
    r = requests.get(f"{GS}/workspaces/NTNU/datastores/rovdb/featuretypes.json",
                     auth=AUTH)
    if r.status_code == 200:
        layers = [ft["name"] for ft in r.json().get("featureTypes", {}).get("featureType", [])]
        print(f"  Published layers ({len(layers)}):")
        for l in sorted(layers):
            print(f"    - NTNU:{l}")
    else:
        print(f"  Could not list layers: {r.status_code}")

    print("\nDone! GeoServer web UI: http://localhost:8080/geoserver/web/")
    print("WMS endpoint:           http://localhost:8080/geoserver/NTNU/wms")
    print("WFS endpoint:           http://localhost:8080/geoserver/NTNU/wfs")


main()
