import unittest
from unittest.mock import MagicMock, call, patch

from PIL import Image
from pipeline import build_combined_png
from pipeline import process_image


class ReportTests(unittest.TestCase):
    def test_index_failure_fails_the_whole_report(self):
        with patch(
            "pipeline.fetch_index_png",
            side_effect=RuntimeError("thumbnail unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "thumbnail unavailable"):
                build_combined_png(None, "Plot A", "2026-10-01", None, ["NDVI"])

    def test_builds_report_with_only_available_panels(self):
        with patch(
            "pipeline.fetch_index_png",
            return_value=Image.new("RGB", (64, 64), "green"),
        ):
            report = build_combined_png(
                None,
                "Plot A",
                "2026-10-01",
                None,
                ["NDVI", "NDMI", "NDWI", "EVI", "GCI"],
            )

        report_image = Image.open(report)
        self.assertEqual(report_image.size, (1024, 1606))

    def test_landsat_processing_does_not_compute_ndre(self):
        image = MagicMock()
        image.select.return_value.projection.return_value = "projection"
        image.resample.return_value = image
        image.reproject.return_value = image
        image.clip.return_value = image

        process_image(image, None, "Landsat 8")

        self.assertNotIn(call("NIR", "RE1"), image.normalizedDifference.call_args_list)


if __name__ == "__main__":
    unittest.main()
