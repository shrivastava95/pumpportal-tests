# pumpportal-tests

This script contains a bunch of tests and sample scripts that will assist us in using pumpportal and relying on it as we move forward.

1. `1_subscribeUnsubsribeTokenTrades/temp.py` - Receive new trade push notifications on a dynamically changing pool of tokens to be monitored (read from `tokens.txt`). Reuses a single websocket connection, in accordance with the PumpPortal realtime Data API recommendations.
2. `2_testRealTimeBehaviour/` - Scripts related to testing real-time data handling:
    * `app.py` - Runs a web application (Flask + SSE) that monitors an initial set of Solana tokens from DexScreener via the PumpPortal trade stream. It calculates USD market cap using polled CoinGecko SOL price, allows adding/removing tokens via the UI, and displays results sorted alphabetically with flashing buy/sell indicators. To run, install dependencies with `pip install -r pumpportal-tests/2_testRealTimeBehaviour/requirements.txt`, then execute `python pumpportal-tests/2_testRealTimeBehaviour/app.py` and navigate to `http://localhost:5001`.
    * `requirements.txt` - Python package requirements for the scripts in this directory.
