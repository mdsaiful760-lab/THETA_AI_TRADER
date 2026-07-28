# ============================================================
# THETA AI TRADER
# RISK MANAGEMENT ENGINE
# ============================================================


class RiskManager:
    """
    Central risk-management system for THETA AI TRADER.

    This module does NOT place orders.
    It only calculates and validates trading risk.
    """

    def __init__(
        self,
        capital,
        max_risk_per_trade_pct=1.0,
        max_daily_loss_pct=2.0,
    ):
        self.capital = float(capital)

        self.max_risk_per_trade_pct = float(
            max_risk_per_trade_pct
        )

        self.max_daily_loss_pct = float(
            max_daily_loss_pct
        )

    # --------------------------------------------------------
    # MAXIMUM RISK PER TRADE
    # --------------------------------------------------------

    def max_trade_risk(self):

        return self.capital * (
            self.max_risk_per_trade_pct / 100
        )

    # --------------------------------------------------------
    # MAXIMUM DAILY LOSS
    # --------------------------------------------------------

    def max_daily_loss(self):

        return self.capital * (
            self.max_daily_loss_pct / 100
        )

    # --------------------------------------------------------
    # POSITION SIZE
    # --------------------------------------------------------

    def calculate_lots(
        self,
        risk_per_lot,
        max_lots=10,
    ):

        if risk_per_lot <= 0:
            return 0

        allowed_risk = self.max_trade_risk()

        lots = int(
            allowed_risk // risk_per_lot
        )

        return max(
            0,
            min(lots, max_lots)
        )

    # --------------------------------------------------------
    # STOP LOSS PRICE
    # --------------------------------------------------------

    def calculate_stop_loss(self, premium, stop_loss_pct=30):
        """
        Calculate stop-loss price for a short option.
        """

        if premium <= 0:
            return 0

        return premium * (1 + stop_loss_pct / 100)

        # --------------------------------------------------------
    # STRANGLE RISK PER LOT
    # --------------------------------------------------------

    def calculate_strangle_risk(
        self,
        ce_premium,
        pe_premium,
        lot_size,
        stop_loss_pct=30,
    ):
        """
        Calculate estimated risk for one short-strangle lot.
        """

        ce_sl = self.calculate_stop_loss(
            ce_premium,
            stop_loss_pct,
        )

        pe_sl = self.calculate_stop_loss(
            pe_premium,
            stop_loss_pct,
        )

        ce_risk = (ce_sl - ce_premium) * lot_size
        pe_risk = (pe_sl - pe_premium) * lot_size

        total_risk = ce_risk + pe_risk

        return {
            "ce_stop_loss": ce_sl,
            "pe_stop_loss": pe_sl,
            "ce_risk": ce_risk,
            "pe_risk": pe_risk,
            "risk_per_lot": total_risk,
        }
# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    risk = RiskManager(
        capital=2_000_000,
        max_risk_per_trade_pct=1,
        max_daily_loss_pct=2,
    )

    test_risk = risk.calculate_strangle_risk(
        ce_premium=14.05,
        pe_premium=11.90,
        lot_size=65,
        stop_loss_pct=30,
    )

    recommended_lots = risk.calculate_lots(
        risk_per_lot=test_risk["risk_per_lot"],
        max_lots=10,
    )

    print("=" * 60)
    print("🛡️ THETA AI TRADER — RISK MANAGER")
    print("=" * 60)

    print(
        f"Capital            : ₹{risk.capital:,.0f}"
    )

    print(
        f"Max Risk / Trade   : ₹{risk.max_trade_risk():,.0f}"
    )

    print(
        f"Max Daily Loss     : ₹{risk.max_daily_loss():,.0f}"
    )

    print(
        f"CE Stop Loss       : ₹{test_risk['ce_stop_loss']:.2f}"
    )

    print(
        f"PE Stop Loss       : ₹{test_risk['pe_stop_loss']:.2f}"
    )

    print(
        f"CE Risk / Lot      : ₹{test_risk['ce_risk']:,.2f}"
    )

    print(
        f"PE Risk / Lot      : ₹{test_risk['pe_risk']:,.2f}"
    )

    print(
        f"Total Risk / Lot   : ₹{test_risk['risk_per_lot']:,.2f}"
    )

    print(
        f"Recommended Lots   : {recommended_lots}"
    )

    print("=" * 60)