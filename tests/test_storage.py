import tempfile
import unittest
from pathlib import Path

from storage import (
    connect,
    get_observations,
    has_observation,
    image_is_complete,
    save_observation,
    update_delivery_status,
)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.connection = connect(Path(self.temp_dir.name) / "history.db")

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_saves_and_retrieves_plot_history(self):
        save_observation(
            self.connection,
            {
                "plot_name": "Plot A",
                "image_key": "Sentinel-2:scene-1",
                "image_date": "2026-10-01",
                "satellite": "Sentinel-2",
                "ndvi": 0.62,
                "ndmi": 0.21,
                "ndwi": -0.10,
                "ndre": 0.35,
                "evi": 0.41,
                "gci": 2.4,
                "report_path": "data/reports/plot-a.png",
                "delivery_status": "pending",
            },
        )

        self.assertTrue(has_observation(self.connection, "Plot A", "Sentinel-2:scene-1"))
        self.assertTrue(image_is_complete(self.connection, "Sentinel-2:scene-1", ["Plot A"]))
        self.assertEqual(get_observations(self.connection, ["Plot A"])[0]["ndvi"], 0.62)

    def test_incomplete_images_and_delivery_status_are_tracked(self):
        save_observation(
            self.connection,
            {
                "plot_name": "Plot A",
                "image_key": "Landsat 8:scene-2",
                "image_date": "2026-10-02",
                "satellite": "Landsat 8",
                "ndvi": 0.5,
                "ndmi": 0.1,
                "ndwi": -0.1,
                "evi": 0.3,
                "gci": 2.0,
                "report_path": "data/reports/plot-a.png",
                "delivery_status": "pending",
            },
        )
        update_delivery_status(
            self.connection, "Plot A", "Landsat 8:scene-2", "failed", "network error"
        )

        self.assertFalse(
            image_is_complete(
                self.connection, "Landsat 8:scene-2", ["Plot A", "Plot B"]
            )
        )
        row = get_observations(self.connection, ["Plot A"])[0]
        self.assertEqual(row["ndre"], None)
        self.assertEqual(row["delivery_status"], "failed")
        self.assertEqual(row["delivery_error"], "network error")


if __name__ == "__main__":
    unittest.main()
