# pumpportal-tests

This script contains a bunch of tests and sample scripts that will assist us in using pumpportal and relying on it as we move forward.

1. `1_subscribeUnsubsribeTokenTrades/temp.py` - Receive new trade push notifications on a dynamically changing pool of tokens to be monitored (read from `tokens.txt`). Reuses a single websocket connection, in accordance with the PumpPortal realtime Data API recommendations.
2. `2_testRealTimeBehaviour/` - Scripts related to testing real-time data handling:
    * `app.py` - Runs a web application (Flask + SSE) that monitors an initial set of Solana tokens from DexScreener via the PumpPortal trade stream. It calculates USD market cap using polled CoinGecko SOL price, allows adding/removing tokens via the UI, and displays results sorted alphabetically with flashing buy/sell indicators. To run, install dependencies with `pip install -r pumpportal-tests/2_testRealTimeBehaviour/requirements.txt`, then execute `python pumpportal-tests/2_testRealTimeBehaviour/app.py` and navigate to `http://localhost:5001`.
    * `requirements.txt` - Python package requirements for the scripts in this directory.
3. `3_testNewTokenDiscovery/` - Scripts for monitoring new token creation events:
    * `app.py` - Runs a web application (Flask + SSE) that subscribes to PumpPortal's new token creation events. It displays information cards for each new token received, showing details like mint address, name, symbol, and the time it was received. To run, install dependencies with `pip install -r pumpportal-tests/3_testNewTokenDiscovery/requirements.txt`, then execute `python pumpportal-tests/3_testNewTokenDiscovery/app.py` and navigate to `http://localhost:5002`.
    * `requirements.txt` - Python package requirements for the new token discovery app (same as `2_testRealTimeBehaviour`).
4. `4_combinedMonitor/` - Scripts for combined monitoring and logging:
    * `app.py` - Connects to PumpPortal, subscribes to both new token events (`subscribeNewToken`) and trade events (`subscribeTokenTrade`). It dynamically subscribes to trades for newly discovered tokens (identified via `txType: 'create'` events) and logs all received buy/sell trades to `trades.db`. The database includes a special column `tracked_token_count_at_event` recording how many tokens were being tracked when the trade occurred. Run with `python pumpportal-tests/4_combinedMonitor/app.py`.
    * `verify_db.py` - A utility script to connect to `trades.db` and print all logged trade rows for verification. Run with `python pumpportal-tests/4_combinedMonitor/verify_db.py`.
    * `trades.db` - The SQLite database file where trade data is logged by `app.py`.
