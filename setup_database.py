# setup_database.py — v0.6
import sqlite3

DB_NAME = 'usuarios.db'

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
  id_usuario        TEXT PRIMARY KEY,
  nombre            TEXT,
  apodo             TEXT,
  preferencias_json TEXT,
  fecha_registro    INTEGER
);

CREATE TABLE IF NOT EXISTS dominio_usuario (
  id_usuario     TEXT NOT NULL,
  concepto_id    TEXT NOT NULL,
  prob_maestria  REAL DEFAULT 0.0,   -- arrancar en 0.0 (no 0.25)
  intentos       INTEGER DEFAULT 0,
  PRIMARY KEY (id_usuario, concepto_id)
);

CREATE TABLE IF NOT EXISTS sesiones (
  sesion_id     TEXT PRIMARY KEY,
  id_usuario    TEXT NOT NULL,
  objetivo      TEXT NOT NULL,
  mundo         TEXT,
  grado         TEXT,
  tema          TEXT,                -- NUEVO/ALINEADO
  estado        TEXT DEFAULT 'activa',
  fecha_inicio  INTEGER NOT NULL,
  fecha_fin     INTEGER
);

CREATE TABLE IF NOT EXISTS historial_respuestas (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  sesion_id       TEXT,
  id_usuario      TEXT NOT NULL,
  concepto_id     TEXT NOT NULL,
  item_id         TEXT NOT NULL,
  correcta        INTEGER NOT NULL CHECK(correcta IN (0,1)),
  opcion_elegida  TEXT,
  dificultad_item TEXT,
  pistas_usadas   INTEGER DEFAULT 0,
  timestamp       INTEGER NOT NULL,
  objetivo        TEXT,
  mundo           TEXT,
  grado           TEXT,              -- NUEVO/ALINEADO
  tema            TEXT,              -- NUEVO/ALINEADO
  tiempo_ms       INTEGER,
  me_gusto        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_hist_user_concept ON historial_respuestas(id_usuario, concepto_id);

CREATE TABLE IF NOT EXISTS feedback_sesion (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  sesion_id    TEXT,
  id_usuario   TEXT,
  calificacion INTEGER,
  comentario   TEXT,
  timestamp    INTEGER
);

CREATE TABLE IF NOT EXISTS badges (
  badge_id    TEXT PRIMARY KEY,
  nombre      TEXT NOT NULL,
  descripcion TEXT
);
CREATE TABLE IF NOT EXISTS user_badges (
  id_usuario  TEXT NOT NULL,
  badge_id    TEXT NOT NULL,
  fecha       INTEGER,
  PRIMARY KEY (id_usuario, badge_id)
);
"""

def create_database():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    conn.commit(); conn.close()
    print("✅ Esquema verificado/creado en", DB_NAME)

if __name__ == "__main__":
    create_database()
