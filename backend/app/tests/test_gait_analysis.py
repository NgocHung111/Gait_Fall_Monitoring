import unittest

from backend.app.services.gait_analysis import analyze_gait


class GaitAnalysisTests(unittest.TestCase):
    def test_calculates_cadence_interval_and_coefficient_of_variation(self) -> None:
        metrics = analyze_gait(step_timestamps_ms=[1000, 2000, 3000, 4000])

        self.assertEqual(metrics.step_intervals_ms, [1000.0, 1000.0, 1000.0])
        self.assertEqual(metrics.cadence_steps_per_minute, 60.0)
        self.assertEqual(metrics.mean_step_interval_ms, 1000.0)
        self.assertEqual(metrics.variability_cv_percent, 0.0)

    def test_returns_unavailable_metrics_without_step_timing(self) -> None:
        metrics = analyze_gait()

        self.assertIsNone(metrics.cadence_steps_per_minute)
        self.assertIsNone(metrics.mean_step_interval_ms)
        self.assertIsNone(metrics.variability_cv_percent)

    def test_rejects_non_increasing_timestamps(self) -> None:
        with self.assertRaises(ValueError):
            analyze_gait(step_timestamps_ms=[1000, 1000])

    def test_rejects_both_timing_inputs(self) -> None:
        with self.assertRaises(ValueError):
            analyze_gait(
                step_timestamps_ms=[1000, 2000],
                step_intervals_ms=[1000],
            )


if __name__ == "__main__":
    unittest.main()
