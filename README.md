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

## Teknologi

- [Docker](https://docker.com)
- [PostGIS](https://postgis.net)
- [GeoServer](https://geoserver.org)
- [Leaflet.js](https://leafletjs.com)
- [imcpy](https://github.com/oysstu/imcpy) – Python-bindingar for IMC-protokollen EOF
