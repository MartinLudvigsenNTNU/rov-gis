cat > ~/rov-gis/README.md << 'EOF'
# ROV/AUV GIS Demonstrator – NTNU

Ein prototype for innsamling, lagring og visualisering av ROV/AUV-loggdata i ein skybasert GIS-teneste.

## Formål

Demonstrere ein fullstendig pipeline frå råloggfiler (DUNE/IMC LSF-format) til interaktivt webkart. 
Prosjektet er meint som eit lærings- og demonstrasjonsgrunnlag for ei framtidig operativ teneste.

## Arkitektur
## Tenester

| Teneste | Port | Beskriving |
|---|---|---|
| Leaflet webkart | 8000 | Interaktivt kart |
| GeoServer | 8080 | Kartmotor (WMS/WFS) |
| PostGIS | 5432 | Spatial database |

## Kom i gang

### Krav
- Docker Desktop
- Python 3.11 (for lokal testing)

### Start tenestene
```bash
docker compose up -d
```

### Last inn loggfiler
Legg LSF-filer i `Data/`-mappa og kjør:
```bash
docker compose up --build ingestion
```

### Opne kartet
## Dataformat

Støttar DUNE/IMC LSF-format (`.lsf`) frå LSTS-verktøykjeda.
Posisjon er rekna ut frå `EstimatedState`-meldingar med NED-offset konvertert til WGS-84.

## Bakgrunnskart

- CartoCDN (lyst/mørkt)
- Esri satellitt
- GEBCO batymetri
- Kartverket topografisk
- EMODnet batymetri

## Neste steg

- [ ] Nginx reverse proxy (løyser CORS)
- [ ] S3-kobling for automatisk innlasting
- [ ] Støtte for ROV-loggformat
- [ ] Tidsserie-kontroll i kartet
- [ ] Flytte til NTNU OpenStack
- [ ] TopoSvalbard (NPI) som bakgrunnskart — NPI tilbyr WMTS i EPSG:25833 (native UTM-33N, ingen reprosjeksjon). Krev proj4leaflet for at Leaflet skal handtere ikkje-Mercator CRS, og eige EPSG:25833 gridset i GeoWebCache for at AUV-WMS-lag skal cachast riktig. GWC-seeding via REST-API fungerer ikkje for custom gridsets i GeoServer 2.24 — tiles må seedast via ekstern HTTP eller oppgraderast til GeoServer 2.25+.
- [ ] lauv-fridtjof kamerabilete — kameraet lagrar JPEG-filer direkte til disk utanfor LSF-straumen (ingen `CompressedImage`/IMC-melding i loggen). Ingestion-pipeline må utvidast til å indeksera bildefiler frå tokt-mappene, kopla dei til GPS-posisjon via tidsstempel mot `EstimatedState`, og lagra bildeposisjon i PostGIS for visning i kartet.
- [ ] lauv-marie sonardata — LSF-loggane inneheld ukjende IMC-ID-ar 2023 og 2024 (truleg `SatellitesInView`/`GnssHwMon` frå ein nyare IMC-versjon), pluss `DevDataBinary` med binær sonardata. For å visualisera sidescan/multibeam krevst dekoding av den binære straumen (proprietært format eller `SonarData`-pakker) og konvertering til GeoTIFF eller WCS-lag i GeoServer.
- [ ] Fullstendig sensor-ingestion — les og lagra følgjande sensorar per punkt i `auv_tracks`: Klorofyll-a (IMC 289, harold/thor/roald), Turbiditet (IMC 288, thor/roald), Oksygen (IMC 295, roald), CDOM (IMC 903, harold), Optisk tilbakespredning (IMC 904, harold), `sonar_active` boolean (IMC 276 for thor/fridtjof, IMC 2023 for marie), `camera_active` boolean (IMC 277 for fridtjof). Merk: ikkje alle farkoster bereknar salthaldighet sjølv om dei loggar konduktivitet (lauv-thor manglar Salinity-melding).
- [ ] Sensor × år dashboard-tabell — statisk tabell med sensorar som rader og år som kolonnar. Verdiar: akkumulerte km per sensor per år på tvers av farkoster (PostGIS `track_km_wfs` WFS-lag med `vessel_transit=false`). Sensor-til-farkost-mapping basert på `imc_meldingar.xlsx`-analysen.
- [ ] Grafisk fil-opplasting i dashboard — Enkel "Last inn nye loggfiler"-boks i `dashboard.html`. Bruker vel LSF/LSF.GZ-filer, år, farkost og kampanje. Krev FastAPI-backend for å ta imot filer og køyre ingestion. Alternativt: knapp som triggar ingestion på filer som allereie ligg i `Data/`-mappa på serveren.

## Teknologi

- [Docker](https://docker.com)
- [PostGIS](https://postgis.net)
- [GeoServer](https://geoserver.org)
- [Leaflet.js](https://leafletjs.com)
- [imcpy](https://github.com/oysstu/imcpy) – Python-bindingar for IMC-protokollen EOF
