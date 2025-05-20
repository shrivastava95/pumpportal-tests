import asyncio
import websockets
import json
import sqlite3
import os
import time
import socket
from websockets.connection import State # For checking websocket state

# Define Constants
PUMPORTAL_URI = "wss://pumpportal.fun/api/data"
DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')
INITIAL_TOKENS_FILE = "initial_tokens.txt" # For loading tokens at startup
RECONNECT_DELAY_SECONDS = 10

# Initialize Database Function
def init_db():
    """Initializes the SQLite database and creates the trades table if it doesn't exist."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            signature TEXT PRIMARY KEY,
            mint TEXT NOT NULL,
            traderPublicKey TEXT,
            txType TEXT CHECK(txType IN ('buy', 'sell')),
            tokenAmount REAL,
            solAmount REAL,
            tokensInPool REAL,
            solInPool REAL,
            marketCapSol REAL,
            pool TEXT,
            tracked_token_count_at_event INTEGER NOT NULL, 
            received_timestamp REAL DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        print(f"[DB] Database initialized successfully at {DB_PATH}")
    except sqlite3.Error as e:
        print(f"[DB Error] Could not initialize database: {e}")
    finally:
        if conn:
            conn.close()

def get_existing_mints_from_db(db_path_val):
    """Queries the database for all unique mint addresses from the trades table."""
    mints = set()
    conn = None
    try:
        conn = sqlite3.connect(db_path_val)
        cursor = conn.cursor()
        # Ensure the table exists before trying to select from it, though init_db should handle this.
        cursor.execute("CREATE TABLE IF NOT EXISTS trades (mint TEXT)") # Minimal check, init_db is more complete
        cursor.execute("SELECT DISTINCT mint FROM trades")
        rows = cursor.fetchall()
        for row in rows:
            if row[0]: # Ensure mint is not None or empty
                mints.add(row[0])
        if mints:
            print(f"[DB Load] Loaded {len(mints)} unique mints from existing database at {db_path_val}.")
        else:
            print(f"[DB Load] No existing mints found in database, or trades table is empty/not found for mints at {db_path_val}.")
    except sqlite3.Error as e:
        print(f"[DB Load Error] Could not load mints from {db_path_val}: {e}")
    finally:
        if conn:
            conn.close()
    return mints

# Call init_db() to ensure DB is ready
init_db()

# Shared State
tracked_tokens = set() # Holds mint addresses of all tokens we are subscribed to for trades

async def log_trade_to_db(data):
    """Logs a trade event to the SQLite database."""
    global tracked_tokens
    signature = data.get('signature')
    mint = data.get('mint')
    
    if not signature or not mint:
        print(f"[DB Log] Received trade data without signature or mint: {data}")
        return
        
    trader_pk = data.get('traderPublicKey')
    tx_type = data.get('txType') # Should be 'buy' or 'sell'
    token_amount = data.get('tokenAmount')
    sol_amount = data.get('solAmount')
    tokens_in_pool = data.get('tokensInPool')
    sol_in_pool = data.get('solInPool')
    mc_sol = data.get('marketCapSol')
    pool = data.get('pool')
    current_total_tracked_count = len(tracked_tokens)
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()
        trade_data_tuple = (
            signature, mint, trader_pk, tx_type, token_amount,
            sol_amount, tokens_in_pool, sol_in_pool, mc_sol, pool,
            current_total_tracked_count
        )
        cursor.execute("INSERT INTO trades (signature, mint, traderPublicKey, txType, tokenAmount, solAmount, tokensInPool, solInPool, marketCapSol, pool, tracked_token_count_at_event) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", trade_data_tuple)
        conn.commit()
        print(f"[DB Log] Logged {tx_type} for {mint} (Sig: {signature[:6]}...) by {trader_pk}. SOL: {sol_amount:.4f}, MCapSOL: {mc_sol:.2f}. Total tracked: {current_total_tracked_count}") # Can be noisy
    except sqlite3.IntegrityError:
        # Common if the same signature is received again, ignore silently.
        pass
    except sqlite3.Error as e:
        print(f"[DB Log Error] Failed to log trade {signature}: {e}")
    finally:
        if conn:
            conn.close()

async def subscribe_to_tokens(websocket, tokens_to_subscribe, listener_name="WS"):
    """Helper to subscribe to a list of tokens."""
    if not tokens_to_subscribe:
        return
    token_list = list(tokens_to_subscribe)
    # PumpPortal might have limits on how many tokens can be in one subscribe request.
    # Let's use a conservative chunk size if needed, though for initial load it might be fine.
    # For simplicity here, sending all at once. If issues, chunking can be added.
    print(f"[{listener_name}] Subscribing to trades for {len(token_list)} token(s).")
    await websocket.send(json.dumps({"method": "subscribeTokenTrade", "keys": token_list}))


async def main_listener():
    global tracked_tokens

    # Load initial tokens from file
    mints_from_file = set()
    if os.path.exists(INITIAL_TOKENS_FILE):
        try:
            with open(INITIAL_TOKENS_FILE, "r") as f:
                mints_from_file = {line.strip() for line in f if line.strip() and not line.startswith("#")}
            if mints_from_file:
                print(f"[INIT] Loaded {len(mints_from_file)} tokens from {INITIAL_TOKENS_FILE}.")
        except Exception as e:
            print(f"[INIT] Error loading {INITIAL_TOKENS_FILE}: {e}")

    # Load existing unique mints from the database
    mints_from_db = get_existing_mints_from_db(DB_PATH)
    # mints_from_db will be an empty set if DB is new or has no mints, or if an error occurs.

    # Combine tokens from file and DB and update the global tracked_tokens set
    # This happens once at script startup.
    tracked_tokens.update(mints_from_file)
    tracked_tokens.update(mints_from_db)

    if tracked_tokens:
        print(f"[INIT] Initialized with {len(tracked_tokens)} unique tokens to track (from file and/or DB).")
    else:
        print(f"[INIT] No initial tokens loaded from file or DB. Will discover new tokens.")
    
    initial_subscription_done_for_session = False

    while True:
        websocket = None
        try:
            print(f"[WS] Attempting to connect to {PUMPORTAL_URI}...")
            websocket = await websockets.connect(PUMPORTAL_URI, ping_interval=20, ping_timeout=20)
            print(f"[WS] Connected to {PUMPORTAL_URI}")

            # Sanity check: Ensure the websocket state is OPEN after connection
            if not (hasattr(websocket, 'state') and websocket.state == State.OPEN):
                actual_state = getattr(websocket, 'state', 'N/A (attribute missing)')
                print(f"[WS CRITICAL] Post-connection check failed: Websocket 'state' is not OPEN (is {actual_state}) or attribute 'state' is missing.")
                print(f"[WS DEBUG] Attributes of websocket object: {dir(websocket)}")
                if hasattr(websocket, 'protocol'):
                    print(f"[WS DEBUG] Attributes of websocket.protocol object: {dir(websocket.protocol)}")
                    protocol_state = getattr(websocket.protocol, 'state', 'N/A (attribute missing)')
                    protocol_open = getattr(websocket.protocol, 'open', 'N/A (attribute missing)')
                    print(f"[WS DEBUG] websocket.protocol.state: {protocol_state}")
                    print(f"[WS DEBUG] websocket.protocol.open: {protocol_open}")
                else:
                    print("[WS DEBUG] websocket.protocol attribute does not exist.")
                # Force a connection closed error to trigger reconnection logic
                raise websockets.exceptions.ConnectionClosedError(None, None)

            initial_subscription_done_for_session = False # Reset for new connection

            # 1. Subscribe to New Token events
            await websocket.send(json.dumps({"method": "subscribeNewToken"}))
            print("[WS] Subscribed to new token events (subscribeNewToken).")

            # 2. Subscribe to trades for all currently tracked tokens (from file, DB, and dynamically added)
            #    This ensures that on any (re)connection, we try to establish subscriptions for everything we know.
            if tracked_tokens:
                print(f"[WS] Attempting to subscribe/resubscribe to trades for {len(tracked_tokens)} tracked token(s).")
                await subscribe_to_tokens(websocket, tracked_tokens, listener_name="TrackedTokensSync")
                initial_subscription_done_for_session = True # Mark that initial batch sent for this connection
            # else:
                # print("[WS] No tokens currently in tracked_tokens to subscribe to for trades yet.")


            async for message in websocket:
                try:
                    data = json.loads(message)
                    # print(f"[WS RECV] {data}") # Very verbose debugging

                    mint = data.get('mint')
                    tx_type = data.get('txType')
                    signature = data.get('signature')

                    if tx_type == 'create' and mint: # New token discovery from subscribeNewToken
                        if mint not in tracked_tokens:
                            print(f"[NEW TOKEN] Discovered: {data.get('name', '')} ({data.get('symbol', '')}) Mint: {mint}. Subscribing to its trades.")
                            tracked_tokens.add(mint)
                            await subscribe_to_tokens(websocket, {mint}, listener_name="NewTokenDynamic")
                            # Optional: Log the creation event to DB if schema supports it.
                        # else:
                            # print(f"[NEW TOKEN] Rediscovered known token: {mint}. Already tracked.")

                    elif tx_type in ('buy', 'sell') and mint and signature: # Actual trade event
                        if mint in tracked_tokens: # Only log trades for tokens we are explicitly tracking
                            await log_trade_to_db(data)
                        # else:
                            # print(f"[WS] Received trade for untracked token {mint}. Ignoring.")


                    elif "Successfully subscribed to token trades" in data.get("message", ""):
                        # Check if this confirmation pertains to the initial batch for this session
                        # This helps in not over-logging if server sends confirmations for individual tokens later
                        if initial_subscription_done_for_session:
                             print(f"[WS] Confirmed trade subscription (likely for initial batch): {data.get('message')}")
                             initial_subscription_done_for_session = False # Reset after first confirmation batch
                        else:
                             print(f"[WS] Confirmed trade subscription (dynamic or other): {data.get('message')}")
                    elif "Successfully subscribed to new token events" in data.get("message", ""):
                        print(f"[WS] Confirmed new token event subscription: {data.get('message')}")
                    elif data.get("type") == "error":
                        print(f"[WS ERROR Server] Received server error: {data.get('message', 'No message provided')}")
                    # else:
                        # print(f"[WS INFO] Other message: {data}")

                except json.JSONDecodeError:
                    print(f"[WS Warning] Received non-JSON message: {message[:200]}...")
                except Exception as e:
                    print(f"[WS Error] Error processing message: {e} (Data: {data if 'data' in locals() else 'N/A'})")

        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
            status = "gracefully" if isinstance(e, websockets.exceptions.ConnectionClosedOK) else f"unexpectedly ({type(e).__name__})"
            print(f"[WS Error] Connection closed {status}. Code: {e.code if hasattr(e, 'code') else 'N/A'}. Reconnecting in {RECONNECT_DELAY_SECONDS} seconds...")
        except websockets.exceptions.InvalidStatus as e: # Updated from InvalidStatusCode
             print(f"[WS Error] Connection failed: Invalid Status Code {e.response.status_code}. Check URI. Retrying in {RECONNECT_DELAY_SECONDS*2}s...")
             await asyncio.sleep(RECONNECT_DELAY_SECONDS) # Extra delay for status code issues
        except ConnectionRefusedError: # Specific error for connection refused
            print(f"[WS Error] Connection refused. Server may be down or URI incorrect. Reconnecting in {RECONNECT_DELAY_SECONDS*2} seconds...")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS) # Extra delay
        except socket.gaierror: # More specific error for DNS/network issues
            print(f"[WS Error] Socket/DNS error (gaierror). Check network or URI. Reconnecting in {RECONNECT_DELAY_SECONDS*2} seconds...")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS) # Extra delay
        except Exception as e:
            print(f"[WS Error] An unexpected error occurred in main_listener: {type(e).__name__} - {e}. Reconnecting in {RECONNECT_DELAY_SECONDS} seconds...")
        finally:
            if websocket: # Ensure websocket object exists at all
                try:
                    # Check for 'open' attribute. If it exists, check its value.
                    # Also ensure 'close' method exists before trying to call it.
                    is_open = hasattr(websocket, 'open') and websocket.open
                    can_close = hasattr(websocket, 'close')

                    if is_open and can_close:
                        print("[WS] Attempting to close websocket connection cleanly...")
                        await websocket.close(code=1001, reason='Client shutting down or reconnecting')
                    elif websocket: # websocket object exists but wasn't open or can't be closed as expected
                        print("[WS] Websocket connection was not in a state to be cleanly closed (e.g., already closed, attributes missing). Skipping explicit close call.")
                
                # This broad exception is to catch anything unexpected during the close attempt,
                # including AttributeErrors if the above checks somehow weren't enough, or other errors from close().
                except Exception as e_close: 
                    print(f"[WS Error] Error during websocket cleanup in finally block: {type(e_close).__name__} - {e_close}")
            
            print(f"[WS] Will attempt to reconnect in {RECONNECT_DELAY_SECONDS} seconds.")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    print("Starting Simplified PumpPortal Monitor and Logger...")
    loop = asyncio.get_event_loop()
    try:
        main_task = loop.create_task(main_listener())
        loop.run_until_complete(main_task)
    except KeyboardInterrupt:
        print("\n[MAIN] Script interrupted by user (Ctrl+C).")
        if 'main_task' in locals() and main_task and not main_task.done():
            print("[MAIN] Attempting to cancel main listener task...")
            main_task.cancel()
            try:
                # Give a moment for the task to process cancellation and close websocket
                loop.run_until_complete(asyncio.wait_for(main_task, timeout=5.0))
            except asyncio.CancelledError:
                print("[MAIN] Main listener task was cancelled.")
            except asyncio.TimeoutError:
                print("[MAIN] Timeout waiting for main listener task to cancel. Forcing exit.")
            except Exception as e_cancel:
                print(f"[MAIN] Exception during task cancellation: {e_cancel}")
        print("[MAIN] Exiting...")
    except Exception as e_top:
        print(f"[MAIN Unhandled] Top-level error: {type(e_top).__name__} - {e_top}")
    finally:
        if loop.is_running():
            loop.close()
        print("[MAIN] Script shutdown complete.") 