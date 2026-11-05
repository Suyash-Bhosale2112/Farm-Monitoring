import unittest

from alerts import detect_ndvi_drop


class AlertTests(unittest.TestCase):
    def test_flags_a_drop_from_recent_mean(self):
        history = [
            {"image_date": "2026-10-01", "ndvi": 0.70},
            {"image_date": "2026-10-08", "ndvi": 0.68},
            {"image_date": "2026-10-15", "ndvi": 0.72},
            {"image_date": "2026-10-22", "ndvi": 0.40},
        ]

        result = detect_ndvi_drop(history, threshold=0.15, baseline_days=30)

        self.assertTrue(result["triggered"])
        self.assertAlmostEqual(result["baseline_mean"], 0.70)
        self.assertAlmostEqual(result["drop"], 0.30)

    def test_requires_enough_prior_observations(self):
        history = [
            {"image_date": "2026-10-01", "ndvi": 0.70},
            {"image_date": "2026-10-22", "ndvi": 0.40},
        ]

        self.assertIsNone(detect_ndvi_drop(history, threshold=0.15))

    def test_missing_latest_ndvi_does_not_alert_on_an_old_observation(self):
        history = [
            {"image_date": "2026-10-01", "ndvi": 0.70},
            {"image_date": "2026-10-08", "ndvi": 0.68},
            {"image_date": "2026-10-15", "ndvi": 0.72},
            {"image_date": "2026-10-22", "ndvi": None},
        ]

        self.assertIsNone(detect_ndvi_drop(history, threshold=0.15))


if __name__ == "__main__":
    unittest.main()
