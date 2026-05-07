CREATE TABLE IF NOT EXISTS auv_tracks (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(255),
    vehicle       VARCHAR(100),
    timestamp     TIMESTAMP,
    depth         FLOAT,
    year          INTEGER,
    campaign      VARCHAR(255),
    salinity      FLOAT,
    temperature   FLOAT,
    conductivity  FLOAT,
    source_path   VARCHAR(500),
    geom          GEOMETRY(PointZ, 4326)
);

CREATE INDEX IF NOT EXISTS auv_tracks_geom_idx 
    ON auv_tracks USING GIST(geom);
