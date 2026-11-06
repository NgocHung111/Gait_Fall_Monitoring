import unittest

from fastapi import HTTPException

from app.main import (
    Telemetry,
    get_telemetry_history,
    receive_telemetry,
    telemetry_history,
)


class TelemetryHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        telemetry_history.clear()

    def tearDown(self) -> None:
        telemetry_history.clear()

    def test_records_successful_telemetry_for_dashboard(self) -> None:
        response = receive_telemetry(
            Telemetry(steps=123, step_intervals_ms=[900, 1100])
        )

        history = get_telemetry_history(limit=1)
        self.assertEqual(response.steps, 123)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].steps, 123)
        self.assertEqual(history[0].gait.cadence_steps_per_minute, 60.0)
        self.assertIsNotNone(history[0].timestamp_utc)

    def test_rejects_out_of_range_history_limit(self) -> None:
        with self.assertRaises(HTTPException):
            get_telemetry_history(limit=251)


if __name__ == "__main__":
    unittest.main()
