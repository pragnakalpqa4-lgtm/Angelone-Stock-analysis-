import tempfile
import unittest
from pathlib import Path

from analysis import analyze


class AnalysisTests(unittest.TestCase):
    def run_csv(self, content, window=2):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prices.csv"
            path.write_text(content, encoding="utf-8")
            return analyze(path, window)

    def test_sorts_dates_and_calculates_metrics(self):
        result = self.run_csv("date,close\n2026-01-03,90\n2026-01-01,100\n2026-01-02,120\n")
        self.assertAlmostEqual(result["total_return_pct"], -10)
        self.assertAlmostEqual(result["latest_return_pct"], -25)
        self.assertAlmostEqual(result["max_drawdown_pct"], -25)
        self.assertEqual(result["moving_average"], 105)

    def test_insufficient_window(self):
        result = self.run_csv("date,close\n2026-01-01,100\n2026-01-02,110\n", 20)
        self.assertIsNone(result["moving_average"])

    def test_rejects_bad_inputs(self):
        samples = [
            "date,close\n2026-01-01,100\n2026-01-01,110\n",
            "date,close\n2026-01-01,nan\n2026-01-02,110\n",
            "date,close\n2026-01-01,0\n2026-01-02,110\n",
            "date,close\nwrong,100\n2026-01-02,110\n",
            "date,close\n2026-01-01,100\n",
            "date,price\n2026-01-01,100\n",
        ]
        for sample in samples:
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                self.run_csv(sample)
        with self.assertRaises(ValueError):
            self.run_csv("date,close\n2026-01-01,100\n2026-01-02,110\n", 0)


if __name__ == "__main__":
    unittest.main()

