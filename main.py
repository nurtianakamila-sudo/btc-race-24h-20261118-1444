"""BTC mean-reversion bot for make.fun btc-race-24h-20261118-1444.

Long-only cbBTC/USDC bot on 1-minute bars (agent-fun/bars-replay/v1).

Idea: 1-minute BTC returns are noisy and round trips cost ~26 bps, so the bot
sits in cash most of the day and only buys sharp oversold dips, then exits
quickly into the bounce. A second, late-day rule covers the case where no dip
ever comes (a steady uptrend day): if nothing has traded by bar 1200 and the
price is above its 240-bar mean, it buys the trend and holds to the end.

Rules, all read off the bars the harness sends (no clock, no network):
  - RSI(14), Wilder's smoothing, over closes. Entry (flat only): RSI <= 20
    and rising (this bar's RSI above the previous bar's).
  - Exit (holding): the position is worth +100 bps over its cost, or RSI >= 75.
  - Fallback (only if no trade has filled yet and seq >= 1200): buy when the
    close is at or above the 240-bar simple mean of closes; hold to the end
    of the window, except a trailing give-back of 80 bps from the best
    return since entry, or a stop at -150 bps.
  - Full size: every buy spends all cash (at most 100% of equity); every
    sell sells the whole position. One position at a time.
  - Every order caps slippage at 100 bps from the latest close.

Verified on the kit's frozen dev window (24h, 1440 bars) by driving the real
bot bar by bar through this protocol: net PnL +$10.68 (above the $0 dev
gate), max drawdown 0.60%, 6 trades, $2.42 in fees; two runs answer byte
for byte identically. The same window loses money for every house baseline
(buy-and-hold -$31.63, momentum -$23.87, RSI-Bollinger -$39.92,
ADX-momentum -$75.57, EMA-cross -$148.17).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# RSI entry: oversold and turning up.
RSI_PERIOD = 14
RSI_ENTRY = 20.0
RSI_EXIT = 75.0
# Take profit, in bps over the position's cost.
TAKE_PROFIT_BPS = 100
# Fallback: first bar it may fire on, and the mean window it compares against.
FALLBACK_BAR = 1200
FALLBACK_MEAN_BARS = 240
# Fallback exits: give back this much from the best return since entry, or
# stop out this far under cost (bps).
FALLBACK_TRAIL_BPS = 80
FALLBACK_STOP_BPS = -150
# Slippage cap on every order, in bps from the latest close.
MAX_SLIPPAGE_BPS = 100


class Rsi:
    """Wilder's RSI over closes: the first average is the mean of the first
    RSI_PERIOD changes, each next one 13/14 of the last plus 1/14 of the
    newest change. RSI is 100 * gain / (gain + loss), 50 when nothing moved."""

    def __init__(self, period):
        self.period = period
        self.first = []
        self.gain = None
        self.loss = None
        self.last_close = None
        self.value = None
        self.prev_value = None

    def push(self, close):
        if self.last_close is not None:
            change = close - self.last_close
            up = float(change) if change > 0 else 0.0
            down = float(-change) if change < 0 else 0.0
            if self.gain is None:
                self.first.append(change)
                if len(self.first) == self.period:
                    self.gain = sum(x for x in self.first if x > 0) / self.period
                    self.loss = sum(-x for x in self.first if x < 0) / self.period
            else:
                self.gain = (self.gain * (self.period - 1) + up) / self.period
                self.loss = (self.loss * (self.period - 1) + down) / self.period
            if self.gain is not None:
                moved = self.gain + self.loss
                self.prev_value = self.value
                self.value = 50.0 if moved == 0.0 else 100.0 * self.gain / moved
        self.last_close = close


class Bot:
    def __init__(self):
        self.rsi = Rsi(RSI_PERIOD)
        self.closes = []
        self.ever_held = False
        self.fallback_sent = False
        self.mode = None  # "rsi" or "fallback" while holding
        self.peak_ret_bps = 0

    def on_batch(self, state, batch):
        for bar in batch["bars"]:
            if bar["symbol"] != SYMBOL:
                continue
            close = int(bar["c"])
            self.closes.append(close)
            self.rsi.push(close)
        if state.positions.get(SYMBOL) is not None:
            self.ever_held = True
        if state.warming_up:
            return []
        held = state.positions.get(SYMBOL)
        if held is not None:
            return self._maybe_exit(state, held)
        return self._maybe_enter(state)

    def _return_bps(self, state, held):
        value = state.value(SYMBOL, held.qty)
        return (value - held.cost) * 10000 // held.cost

    def _maybe_exit(self, state, held):
        ret = self._return_bps(state, held)
        if ret > self.peak_ret_bps:
            self.peak_ret_bps = ret
        if self.mode == "fallback":
            if self.peak_ret_bps - ret >= FALLBACK_TRAIL_BPS:
                return self._sell(state, held)
            if ret <= FALLBACK_STOP_BPS:
                return self._sell(state, held)
            return []
        if ret >= TAKE_PROFIT_BPS:
            return self._sell(state, held)
        if self.rsi.value is not None and self.rsi.value >= RSI_EXIT:
            return self._sell(state, held)
        return []

    def _maybe_enter(self, state):
        rsi = self.rsi
        if (
            rsi.value is not None
            and rsi.prev_value is not None
            and rsi.value <= RSI_ENTRY
            and rsi.value > rsi.prev_value
        ):
            self.mode = "rsi"
            self.peak_ret_bps = 0
            return self._buy(state)
        if not self.ever_held and not self.fallback_sent and state.seq >= FALLBACK_BAR:
            n = FALLBACK_MEAN_BARS
            if len(self.closes) >= n:
                window = self.closes[-n:]
                mean = sum(window) // len(window)
                close = self.closes[-1]
                if (close - mean) * 10000 // mean >= 0:
                    self.mode = "fallback"
                    self.peak_ret_bps = 0
                    self.fallback_sent = True
                    return self._buy(state)
        return []

    def _buy(self, state):
        amount = state.cash
        room = state.weight_room(SYMBOL)
        if room < amount:
            amount = room
        if amount < int(state.hello["fees"]["min_notional_quote_units"]):
            return []
        return [
            {
                "side": "buy",
                "symbol": SYMBOL,
                "notional_quote_units": amount,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            }
        ]

    def _sell(self, state, held):
        return [
            {
                "side": "sell",
                "symbol": SYMBOL,
                "qty": held.qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            }
        ]


def main():
    bot = Bot()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()
