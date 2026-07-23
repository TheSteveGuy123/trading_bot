import unittest

from btc_perp_bot import (
    account_snapshot,
    close_position,
    empty_paper_state,
    metrics,
    open_position,
    stop_loss_hit,
    stop_loss_price,
)


class PaperAccountingTests(unittest.TestCase):
    def opened(self, side):
        state = empty_paper_state()
        open_position(state, side, 100.0)
        return state

    def test_profitable_long(self):
        snapshot = account_snapshot(self.opened(1), 110.0)
        self.assertGreater(snapshot["unrealized_pnl"], 0)

    def test_losing_long(self):
        snapshot = account_snapshot(self.opened(1), 90.0)
        self.assertLess(snapshot["unrealized_pnl"], 0)

    def test_profitable_short(self):
        snapshot = account_snapshot(self.opened(-1), 90.0)
        self.assertGreater(snapshot["unrealized_pnl"], 0)

    def test_losing_short(self):
        snapshot = account_snapshot(self.opened(-1), 110.0)
        self.assertLess(snapshot["unrealized_pnl"], 0)

    def test_opening_position_charges_fee_and_reserves_margin(self):
        state = self.opened(1)
        self.assertAlmostEqual(state["fees_paid"], 0.4997501249, places=6)
        self.assertAlmostEqual(state["available_cash"], 9000.0, places=6)
        self.assertAlmostEqual(state["reserved_margin"], 999.5002498751, places=6)

    def test_closing_position_realizes_pnl(self):
        state = self.opened(1)
        gross_pnl, fee = close_position(state, 110.0)
        self.assertGreater(gross_pnl, 0)
        self.assertGreater(fee, 0)
        self.assertAlmostEqual(state["realized_pnl"], gross_pnl, places=6)
        self.assertEqual(state["position"], 0)
        self.assertEqual(state["reserved_margin"], 0)

    def test_reserved_margin_does_not_reduce_equity(self):
        state = self.opened(1)
        snapshot = account_snapshot(state, 100.0)
        self.assertAlmostEqual(snapshot["equity"], 10_000 - snapshot["fees_paid"], places=6)
        self.assertAlmostEqual(snapshot["available_cash"] + snapshot["reserved_margin"], snapshot["equity"], places=6)

    def test_no_closed_trades_returns_na_metrics(self):
        result = metrics([10_000, 9_990, 10_010], [{"action": "LONG", "pnl": 0.0, "fee": 5.0}])
        self.assertIsNone(result["win_rate"])
        self.assertIsNone(result["sharpe_ratio"])

    def test_one_percent_stop_loss_for_long_and_short(self):
        self.assertEqual(stop_loss_price(100.0, 1), 99.0)
        self.assertEqual(stop_loss_price(100.0, -1), 101.0)
        self.assertTrue(stop_loss_hit(100.0, 1, 99.0))
        self.assertTrue(stop_loss_hit(100.0, -1, 101.0))
        self.assertFalse(stop_loss_hit(100.0, 1, 99.01))


if __name__ == "__main__":
    unittest.main()
