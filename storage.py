import os
import sqlite3
from pathlib import Path


INDEX_COLUMNS = ("ndvi", "ndmi", "ndwi", "ndre", "evi", "gci")


def get_database_path():
    path = Path(os.environ.get("FARM_MONITOR_DB", "data/farm_monitoring.db"))
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path


def connect(database_path=None):
    path = Path(database_path) if database_path else get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            plot_name TEXT NOT NULL,
            image_key TEXT NOT NULL,
            image_date TEXT NOT NULL,
            satellite TEXT NOT NULL,
            ndvi REAL,
            ndmi REAL,
            ndwi REAL,
            ndre REAL,
            evi REAL,
            gci REAL,
            report_path TEXT NOT NULL,
            delivery_status TEXT NOT NULL DEFAULT 'not_configured',
            delivery_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (plot_name, image_key)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS observations_date_idx "
        "ON observations (plot_name, image_date)"
    )
    connection.commit()
    return connection


def has_observation(connection, plot_name, image_key):
    row = connection.execute(
        "SELECT 1 FROM observations WHERE plot_name = ? AND image_key = ?",
        (plot_name, image_key),
    ).fetchone()
    return row is not None


def image_is_complete(connection, image_key, plot_names):
    expected_names = set(plot_names)
    if not expected_names:
        return False

    placeholders = ",".join("?" for _ in expected_names)
    row = connection.execute(
        f"""
        SELECT COUNT(DISTINCT plot_name) AS completed
        FROM observations
        WHERE image_key = ? AND plot_name IN ({placeholders})
        """,
        (image_key, *sorted(expected_names)),
    ).fetchone()
    return row["completed"] == len(expected_names)


def save_observation(connection, observation):
    columns = (
        "plot_name",
        "image_key",
        "image_date",
        "satellite",
        *INDEX_COLUMNS,
        "report_path",
        "delivery_status",
        "delivery_error",
    )
    values = tuple(observation.get(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO observations ({', '.join(columns)}) "
        f"VALUES ({placeholders})",
        values,
    )
    connection.commit()


def update_delivery_status(connection, plot_name, image_key, status, error=None):
    connection.execute(
        """
        UPDATE observations
        SET delivery_status = ?, delivery_error = ?
        WHERE plot_name = ? AND image_key = ?
        """,
        (status, error, plot_name, image_key),
    )
    connection.commit()


def get_plot_names(connection):
    rows = connection.execute(
        "SELECT DISTINCT plot_name FROM observations ORDER BY plot_name"
    ).fetchall()
    return [row["plot_name"] for row in rows]


def get_observations(connection, plot_names=None):
    query = "SELECT * FROM observations"
    parameters = ()
    if plot_names:
        placeholders = ",".join("?" for _ in plot_names)
        query += f" WHERE plot_name IN ({placeholders})"
        parameters = tuple(plot_names)
    query += " ORDER BY image_date, created_at"
    rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]
