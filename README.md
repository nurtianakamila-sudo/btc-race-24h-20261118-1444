# btc-race-24h-20261118-1444

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/nurtianakamila-sudo/btc-race-24h-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/nurtianakamila-sudo/btc-race-24h-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A long-only cbBTC/USDC trading bot for make.fun's "Race a BTC trading bot live for 24 hours" bounty ($250 in USDC). Entry by @kamila-nurtiana-terserah-sera.

## How it works

`main.py` (Python 3.13, standard library only, no network, no wall clock, deterministic) speaks the agent-fun/bars-replay/v1 protocol: it reads one 1-minute cbBTC/USDC bar per batch on stdin and answers with buy/sell orders. It starts with $1,000 USDC of paper money, trades long only, and uses the kit's Python SDK shim (`vendor/makefun_bars.py`, copied byte-for-byte) for the wire protocol.

The strategy is a patient mean-reversion bot: 1-minute BTC returns are noisy and a full-size round trip costs about 26 bps in fees, slippage and price impact, so it sits in cash most of the day and only buys sharp oversold dips, then exits quickly into the bounce.

- **Entry:** RSI(14), Wilder's smoothing, over closes. Buy (full size, all cash) when RSI is at or below 20 and rising (this bar's RSI above the previous bar's).
- **Exit:** sell the whole position when it is worth 100 bps over its cost, or when RSI reaches 75.
- **Late-day fallback:** if no trade has filled by bar 1200 and the close is at or above the 240-bar mean of closes, buy the trend and hold to the end of the window, with a trailing give-back exit of 80 bps and a stop at −150 bps. This covers a steady uptrend day, where no oversold dip ever appears.
- Every order caps slippage at 100 bps from the latest close.

## Local result (dev window, 24h, 1440 bars)

Verified with a local replay of the real `main.py` against the kit's frozen dev window (the kit's `grade-local` cannot run Python bots on this machine — its process driver runs Node only and Docker is unavailable — so the bot was driven bar by bar through the same protocol and scored with the harness's own fill math, which reproduces the official grade exactly on the baselines: buy-and-hold −$31.63, momentum −$23.87):

- **Net PnL: +$10.68** (above the $0 dev gate)
- **Max drawdown: 0.60%** (limit 25%)
- **Trades: 6** (3 round trips)
- **Fees paid: $2.42**
- Two runs produce byte-identical replies (deterministic).

For comparison on the same window, every house baseline loses money: buy-and-hold −$31.63, momentum −$23.87, RSI-Bollinger −$39.92, ADX-momentum −$75.57, EMA-cross −$148.17.
