# ============================================================
# THETA AI TRADER
# PAPER ORDER MANAGER
# ============================================================

from datetime import datetime


class PaperOrderManager:

    def __init__(self):
        self.positions = []
        self.closed_trades = []

    # --------------------------------------------------------
    # PAPER SELL
    # --------------------------------------------------------

    def sell_option(
        self,
        symbol,
        premium,
        lots,
        lot_size,
        stop_loss_price,
    ):

        position = {
            "symbol": symbol,
            "side": "SELL",
            "entry_price": float(premium),
            "current_price": float(premium),
            "lots": int(lots),
            "lot_size": int(lot_size),
            "quantity": int(lots * lot_size),
            "stop_loss": float(stop_loss_price),
            "entry_time": datetime.now(),
            "status": "OPEN",
        }

        self.positions.append(position)

        print(f"📝 PAPER SELL : {symbol}")
        print(f"Entry         : ₹{premium:.2f}")
        print(f"Lots          : {lots}")
        print(f"Quantity      : {position['quantity']}")
        print(f"Stop Loss     : ₹{stop_loss_price:.2f}")

        return position

    # --------------------------------------------------------
    # CALCULATE P&L
    # --------------------------------------------------------

    def calculate_pnl(self, position, current_price):

        if position["side"] == "SELL":

            pnl = (
                position["entry_price"] - current_price
            ) * position["quantity"]

        else:
            pnl = (
                current_price - position["entry_price"]
            ) * position["quantity"]

        return pnl

    # --------------------------------------------------------
    # CHECK STOP LOSS
    # --------------------------------------------------------

    def check_stop_loss(self, position, current_price):

        if position["status"] != "OPEN":
            return False

        position["current_price"] = float(current_price)

        if (
            position["side"] == "SELL"
            and current_price >= position["stop_loss"]
        ):
            return True

        return False

    # --------------------------------------------------------
    # CLOSE POSITION
    # --------------------------------------------------------

    def close_position(
        self,
        position,
        exit_price,
        reason="MANUAL EXIT",
    ):

        if position["status"] != "OPEN":
            return None

        pnl = self.calculate_pnl(
            position,
            exit_price,
        )

        position["exit_price"] = float(exit_price)
        position["exit_time"] = datetime.now()
        position["pnl"] = pnl
        position["exit_reason"] = reason
        position["status"] = "CLOSED"

        self.closed_trades.append(position)

        print()
        print(f"🔴 PAPER EXIT : {position['symbol']}")
        print(f"Exit Price    : ₹{exit_price:.2f}")
        print(f"Reason        : {reason}")
        print(f"P&L           : ₹{pnl:,.2f}")

        return pnl