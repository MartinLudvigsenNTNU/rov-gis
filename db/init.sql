CREATE TABLE IF NOT EXISTS auv_tracks (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255),
    vehicle     VARCHAR(50),
    timestamp   TIMESTAMP,
    depth       FLOAT,
    geom        GEOMETRY(PointZ, 4326)
);

CREATE INDEX IF NOT EXISTS auv_tracks_geom_idx 
    ON auv_tracks USING GIST(geom);
