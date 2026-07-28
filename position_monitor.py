# ============================================================
# THETA AI TRADER — PAPER POSITION MONITOR
# ============================================================

from datetime import datetime
import os
import time

from dotenv import load_dotenv
from kiteconnect import KiteConnect
# ============================================================
# KITE CONNECTION — MARKET DATA ONLY
# ============================================================

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")

if not API_KEY:
    raise ValueError("KITE_API_KEY not found in .env")

if not ACCESS_TOKEN:
    raise ValueError("KITE_ACCESS_TOKEN not found in .env")

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# ============================================================
# POSITION MANAGEMENT CONFIGURATION
# ============================================================

COST_SL_BUFFER_PCT = 5.0

class PositionMonitor:

    def __init__(self):
        self.positions = []
    # --------------------------------------------------------
    # FETCH LIVE OPTION PRICE
    # --------------------------------------------------------

    @staticmethod
    def get_live_price(symbol):

        instrument = f"NFO:{symbol}"

        try:
            data = kite.ltp(instrument)

            return float(
                data[instrument]["last_price"]
            )

        except Exception as error:
            print(
                f"⚠️ Could not fetch LTP for {symbol}: {error}"
            )
            return None
    # --------------------------------------------------------
    # ADD POSITION
    # --------------------------------------------------------

    def add_position(
        self,
        symbol,
        entry_price,
        quantity,
        stop_loss_price,
        side="SELL",
    ):
        position = {
            "symbol": symbol,
            "entry_price": float(entry_price),
            "quantity": int(quantity),
            "stop_loss_price": float(stop_loss_price),
            "side": side,
            "status": "OPEN",
            "current_price": float(entry_price),
            "pnl": 0.0,
            "exit_reason": None,
            "created_at": datetime.now(),
        }

        self.positions.append(position)

        return position

    # --------------------------------------------------------
    # CALCULATE P&L
    # --------------------------------------------------------

    @staticmethod
    def calculate_pnl(position, current_price):

        entry = position["entry_price"]
        quantity = position["quantity"]

        if position["side"] == "SELL":
            pnl = (entry - current_price) * quantity
        else:
            pnl = (current_price - entry) * quantity

        return pnl

    # --------------------------------------------------------
    # UPDATE POSITION
    # --------------------------------------------------------

    def update_position(self, symbol, current_price):

        for position in self.positions:

            if (
                position["symbol"] == symbol
                and position["status"] == "OPEN"
            ):

                current_price = float(current_price)

                position["current_price"] = current_price

                position["pnl"] = self.calculate_pnl(
                    position,
                    current_price,
                )

                # SELL option SL triggers when price rises to SL
                if (
                    position["side"] == "SELL"
                    and current_price > position["stop_loss_price"]
                ):
                    position["status"] = "CLOSED"
                    position["exit_reason"] = "STOP LOSS"
                    position["exit_price"] = current_price
                    position["exit_time"] = datetime.now()

                    # Protect the surviving strangle leg
                    self.move_surviving_leg_to_cost(position["symbol"])

                    print(
                        f"\n🛑 PAPER STOP LOSS HIT: {position['symbol']}"
                    )
                    print(f"Exit Price : ₹{current_price:.2f}")
                    print(f"P&L        : ₹{position['pnl']:,.2f}")
                    

                return position

        return None
    # --------------------------------------------------------
    # MOVE SURVIVING LEG SL TO COST
    # --------------------------------------------------------

    def move_surviving_leg_to_cost(self, stopped_symbol):

        for position in self.positions:

            # Ignore the leg that already hit SL
            if position["symbol"] == stopped_symbol:
                continue

            # Only modify positions that are still open
            if position["status"] == "OPEN":

                old_stop_loss = position["stop_loss_price"]

                # Move SL to entry price (cost-to-cost)
                position["stop_loss_price"] = position["entry_price"] * (
    1 + COST_SL_BUFFER_PCT / 100
)

                print(
                    f"\n🛡️ SURVIVING LEG PROTECTED: {position['symbol']}"
                )
                print(
                    f"Old Stop Loss : ₹{old_stop_loss:.2f}"
                )
                print(
    f"New Stop Loss : ₹{position['stop_loss_price']:.2f} "
    f"({COST_SL_BUFFER_PCT:.1f}% BUFFER)"
)

                return position

        return None
    # --------------------------------------------------------
    # TOTAL P&L
    # --------------------------------------------------------

    def total_pnl(self):

        return sum(
            position["pnl"]
            for position in self.positions
        )

    # --------------------------------------------------------
    # OPEN POSITIONS
    # --------------------------------------------------------

    def open_positions(self):

        return [
            position
            for position in self.positions
            if position["status"] == "OPEN"
        ]

    # --------------------------------------------------------
    # DISPLAY
    # --------------------------------------------------------

    def display(self):

        print("\n" + "=" * 72)
        print("🤖 THETA AI TRADER — POSITION MONITOR")
        print("=" * 72)

        if not self.positions:

            print("\nNo paper positions.")
            return

        for position in self.positions:

            print(f"\n{position['symbol']}")
            print("-" * 50)

            print(
                f"Entry       : ₹{position['entry_price']:.2f}"
            )

            print(
                f"Current     : ₹{position['current_price']:.2f}"
            )

            print(
                f"Quantity    : {position['quantity']}"
            )

            print(
                f"Stop Loss   : ₹{position['stop_loss_price']:.2f}"
            )

            print(
                f"P&L         : ₹{position['pnl']:,.2f}"
            )

            print(
                f"Status      : {position['status']}"
            )

            if position["exit_reason"]:

                print(
                    f"Exit Reason : {position['exit_reason']}"
                )

        print("\n" + "-" * 72)

        print(
            f"COMBINED P&L : ₹{self.total_pnl():,.2f}"
        )

        print("=" * 72)


# ============================================================
# PAPER POSITION MONITOR
# ============================================================

if __name__ == "__main__":

    monitor = PositionMonitor()

    # --------------------------------------------------------
    # CURRENT PAPER STRANGLE
    # --------------------------------------------------------

    ce_symbol = "NIFTY26JUL24200CE"
    pe_symbol = "NIFTY26JUL23850PE"

    monitor.add_position(
        symbol=ce_symbol,
        entry_price=14.05,
        quantity=650,          # 10 lots × 65
        stop_loss_price=18.27,
    )

    monitor.add_position(
        symbol=pe_symbol,
        entry_price=11.90,
        quantity=650,          # 10 lots × 65
        stop_loss_price=15.47,
    )

    # --------------------------------------------------------
    # AUTOMATIC PAPER MONITOR
    # --------------------------------------------------------

    REFRESH_SECONDS = 10

    print("\n🤖 THETA AI TRADER — LIVE PAPER MONITOR")
    print(f"⏱ Refresh interval: {REFRESH_SECONDS} seconds")
    print("🔒 PAPER MODE — NO REAL ORDERS")
    print("Press Ctrl+C to stop.\n")

    try:

        while True:

            print(
                f"\n🕒 Update: "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # -----------------------------------------------
            # UPDATE ALL OPEN POSITIONS
            # -----------------------------------------------

            for position in monitor.positions:

                if position["status"] != "OPEN":
                    continue

                symbol = position["symbol"]

                try:

                    ltp = monitor.get_live_price(symbol)

                    if ltp is None:
                        print(
                            f"⚠️ No live price received for {symbol}"
                        )
                        continue

                    monitor.update_position(
                        symbol,
                        ltp,
                    )

                except Exception as e:

                    print(
                        f"⚠️ Price update failed for {symbol}: {e}"
                    )

            # -----------------------------------------------
            # DISPLAY CURRENT POSITION
            # -----------------------------------------------

            monitor.display()

            # -----------------------------------------------
            # CHECK IF EVERYTHING IS CLOSED
            # -----------------------------------------------

            open_positions = [
                position
                for position in monitor.positions
                if position["status"] == "OPEN"
            ]

            if not open_positions:

                print("\n🏁 ALL PAPER POSITIONS CLOSED")
                print(
                    f"Final P&L: ₹{monitor.total_pnl():,.2f}"
                )
                break

            # -----------------------------------------------
            # WAIT BEFORE NEXT UPDATE
            # -----------------------------------------------

            time.sleep(REFRESH_SECONDS)

    except KeyboardInterrupt:

        print("\n\n🛑 PAPER MONITOR STOPPED BY USER")

        monitor.display()

        print("\n🔒 No real Kite orders were placed.")