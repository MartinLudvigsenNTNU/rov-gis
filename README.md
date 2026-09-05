# ROV/AUV GIS Portal – NTNU

> **Status (september 2026): stoppa sandbox — ikkje den kjørande portalen.**
> AT334-portalen i produksjon (`auv.aurlab.marin.ntnu.no/at334`) køyrer no på
> **aurlab-cloud** (MapLibre GL JS + pg_tileserv, prosjektkonfig i `projects/at334.yaml`,
> frontend i `web/template/index.html`). GeoServer/Leaflet-stacken i dette repoet er
> stoppa (verifisert juli 2026), og SLD-stilane og `vehicleConfig.yearColors` her styrer
> ingenting lenger. Repoet er behalde som historisk referanse for den opphavlege
> LSF→PostGIS-pipelinen.

Skybasert portal for innsamling, lagring og visualisering av AUV-loggdata frå NTNU si LAUV-flåte på Svalbard.

**Live:** [https://auv.aurlab.marin.ntnu.no/UNIS-AT334](https://auv.aurlab.marin.ntnu.no/UNIS-AT334)

## Status

Portalen var i drift på NTNU OpenStack med HTTPS (Let's Encrypt) fram til sommaren 2026. Stacken er no stoppa — sjå statusnotatet øvst. Tabellen under gjeld siste driftstilstand.

| | |
|---|---|
| **Farkoster** | lauv-fridtjof, lauv-harald, lauv-roald, lauv-thor, lauv-marie |
| **Dataspenn** | 2017–2025 |
| **Datapunkt** | ~10 millionar GPS-posisjonar |
| **Missionar** | Klassifiserte med 5 utfallskategoriar |

## Arkitektur

```
LSF-loggfiler → Ingestion (Python/imcpy) → PostGIS → GeoServer + GWC → Nginx (HTTPS) → Leaflet
```

System-Nginx terminerer HTTPS med Let's Encrypt-sertifikat og fungerer som reverse proxy.
GeoWebCache (GWC) er integrert i GeoServer og cacher WMS-tiles per farkost og år, slik at
kartvisning forblir rask sjølv for store datasett.

## Tenester

| Teneste | Port | Beskriving |
|---|---|---|
| Nginx (HTTPS) | 443 / 80 | Reverse proxy, serverer web-klienten |
| GeoServer | 8080 (intern) | Kartmotor — WMS via GWC, WFS for dashboard |
| PostGIS | 5432 (intern) | Spatial database med views og mat. views |

## Kom i gang

### Krav
- Docker og Docker Compose
- Python 3.11+ (for lokal testing av ingestion)

### Start tenestene
```bash
cp .env.example .env   # fyll inn credentials
docker compose up -d
python3 setup_geoserver.py   # første gong: set opp workspace, lag og stilar
```

### Last inn loggfiler
Legg LSF-filer i `Data/`-mappa og køyr:
```bash
docker compose up --build ingestion
```
Ingestion oppdaterer alle materialiserte views automatisk etter inlasting.

### Opne portalen
| Side | URL |
|---|---|
| Kart | https://auv.aurlab.marin.ntnu.no/UNIS-AT334 |
| Dashboard | https://auv.aurlab.marin.ntnu.no/UNIS-AT334/dashboard.html |
| Hjelp | https://auv.aurlab.marin.ntnu.no/UNIS-AT334/help.html |

For lokal testing: erstatt domenet med `http://localhost`.

## Dataformat

Støttar DUNE/IMC LSF-format (`.lsf` og `.lsf.gz`) frå LSTS-verktøykjeda.
Posisjon er rekna ut frå `EstimatedState`-meldingar med NED-offset konvertert til WGS-84.

## Mission-klassifisering

Kvar missjon vert automatisk klassifisert basert på `VehicleState`, `PlanControlState` og `EstimatedState`:

| Status | Beskriving |
|---|---|
| `success` | Gjennomført som planlagt, eller alle djupe manøvrar fullført |
| `user_surface` | Operatør avbraut på overflata før djupdykk |
| `user_depth_late` | Operatør avbraut under vatn etter ≥ 30 % av typisk distanse (≥ 0,55 km) |
| `user_depth_early` | Operatør avbraut under vatn før 30 % av typisk distanse (< 0,55 km) |
| `technical` | Missjon enda på grunn av feil (navigasjon, djupgrense, lekkasjesensor o.l.) |

## Bakgrunnskart

- OpenStreetMap (standard)
- TopoSvalbard — NPI topografisk kart (EPSG:25833, via proj4leaflet)
- CartoCDN lyst/mørkt
- Esri satellitt
- GEBCO batymetri
- EMODnet batymetri

## Neste steg

- [ ] S3-kobling for automatisk innlasting av nye loggfiler
- [ ] Støtte for ROV-loggformat
- [ ] Tidsserie-kontroll i kartet (skyv gjennom år animert)
- [ ] Grafisk fil-opplasting i dashboard — knapp som triggar ingestion på filer i `Data/`
- [ ] lauv-fridtjof kamerabilete — indekser JPEG-filer frå tokt-mapper, koble til GPS via tidsstempel, vis i kart
- [ ] lauv-marie sonardata — dekod binær sonarstraum (`DevDataBinary`) til GeoTIFF/WCS-lag
- [ ] Fullstendig sensor-ingestion — klorofyll-a, turbiditet, oksygen, CDOM, optisk tilbakespredning, `sonar_active`, `camera_active` per punkt i `auv_tracks`
- [ ] ML-basert anomalideteksjon — Isolation Forest eller autoenkodar på klorofyll/turbiditet/oksygen-tidsserie for automatisk flagging av interessante segment
- [ ] Brukarstatistikk og analytics — når Feide-innlogging er på plass kan brukar-ID loggast per request. Moglege metrics: unike brukarar per dag, mest søkte område, populære farkoster/år, eksport-frekvens. Kan implementerast med enkel PostgreSQL-logg eller Matomo (sjølvhosta).

## Ferdig

- [x] Pipeline frå LSF-loggfiler til PostGIS (Python + imcpy)
- [x] GeoServer med WMS-lag per farkost og år, filtrert for vessel_transit og aborterte missionar
- [x] GeoWebCache-integrasjon — tiles cacht per farkost × år via CQL_FILTER
- [x] Dashboard med oversikt, farkost-faner og mission status-breakdown
- [x] Spatial søk med rektangel og datofilter, CSV-eksport
- [x] Mission-klassifisering med 5 utfallskategoriar
- [x] TopoSvalbard (NPI) som bakgrunnskart
- [x] Materialiserte views for alle dashboard-statistikkar (rask WFS-respons)
- [x] Distribusjon på NTNU OpenStack
- [x] HTTPS med Let's Encrypt via Certbot
- [x] Nginx reverse proxy med injeksjon av GeoServer-credentials (ingen hemmelege nøklar i frontend)

## Teknologi

- [Docker](https://docker.com)
- [PostGIS](https://postgis.net)
- [GeoServer](https://geoserver.org) + GeoWebCache
- [Leaflet.js](https://leafletjs.com) + proj4leaflet
- [imcpy](https://github.com/oysstu/imcpy) – Python-bindingar for IMC-protokollen
- [Nginx](https://nginx.org) – HTTPS-terminering og reverse proxy

## Visste du?

Meir enn 80 % av verdshava er umappa. Til samanlikning har vi kart over overflata til Mars med høgare oppløysing enn botnen av havet.
