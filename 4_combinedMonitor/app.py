import asyncio
import websockets
import json
import sqlite3
import os
import time

# Define Constants
PUMPORTAL_URI = "wss://pumpportal.fun/api/data"
DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')

# Initialize Database Function
def init_db():
    """Initializes the SQLite database and creates the trades table if it doesn't exist."""
    conn = None # Initialize conn to None
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

# Call init_db() to ensure DB is ready
init_db()

# Shared State
tracked_tokens = set()

# Step 2: WebSocket Connection and Initial Subscriptions
# Step 3: Handling New Token Events (Implemented within main)

async def main():
    """Main function to connect to WebSocket, subscribe, and process messages."""
    global tracked_tokens # Ensure we can modify the global set

    while True:
        try:
            async with websockets.connect(PUMPORTAL_URI, ping_interval=20, ping_timeout=20) as websocket:
                print(f"[WS] Connected to {PUMPORTAL_URI}")

                # Subscribe to New Tokens
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                print("[WS] Subscribed to new token events.")

                # Resubscribe to existing tracked tokens on reconnect
                if tracked_tokens:
                    print(f"[WS] Resubscribing to {len(tracked_tokens)} previously tracked token trades...")
                    await websocket.send(json.dumps({"method": "subscribeTokenTrade", "keys": list(tracked_tokens)}))

                async for message in websocket:
                    try:
                        data = json.loads(message)

                        # Revised Logic: Prioritize structure with signature/mint
                        if 'signature' in data and 'mint' in data:
                            signature = data.get('signature')
                            mint = data.get('mint')
                            tx_type = data.get('txType')
                            
                            if tx_type == 'create':
                                # Handle as New Token Discovery Event
                                if mint and mint not in tracked_tokens:
                                    print(f"[New Token] Discovered via create event: {data.get('name')} ({data.get('symbol')}) - Mint: {mint}. Subscribing to trades.")
                                    tracked_tokens.add(mint)
                                    # Send subscription request for this specific token's trades
                                    await websocket.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))

                            elif tx_type in ('buy', 'sell'):
                                # Handle as Trade Event (Log to DB)
                                
                                # Only log if signature and mint are confirmed present (redundant check, but safe)
                                if not signature or not mint:
                                    continue
                                
                                # Extract other fields needed for DB
                                trader_pk = data.get('traderPublicKey')
                                token_amount = data.get('tokenAmount')
                                sol_amount = data.get('solAmount')
                                tokens_in_pool = data.get('tokensInPool')
                                sol_in_pool = data.get('solInPool')
                                mc_sol = data.get('marketCapSol')
                                pool = data.get('pool')

                                # CRITICAL: Get current tracked token count
                                tracked_count = len(tracked_tokens)

                                # Log to Database
                                conn = None
                                try:
                                    conn = sqlite3.connect(DB_PATH, timeout=10)
                                    cursor = conn.cursor()
                                    
                                    trade_data = (
                                        signature, mint, trader_pk, tx_type, token_amount,
                                        sol_amount, tokens_in_pool, sol_in_pool, mc_sol, pool,
                                        tracked_count
                                    )
                                    
                                    cursor.execute("INSERT INTO trades (signature, mint, traderPublicKey, txType, tokenAmount, solAmount, tokensInPool, solInPool, marketCapSol, pool, tracked_token_count_at_event) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", trade_data)
                                    
                                    conn.commit()
                                    print(f"[DB Log] Logged trade: {signature} (Tracking {tracked_count} tokens)")
                                    
                                except sqlite3.IntegrityError:
                                    pass
                                except sqlite3.Error as e:
                                    print(f"[DB Log Error] Failed to log trade {signature}: {e}")
                                finally:
                                    if conn:
                                        conn.close()

                        # Handle other known message types (e.g., subscription confirmation)
                        elif "Successfully subscribed" in data.get("message", ""):
                             print(f"[Info] Received subscription confirmation: {data.get('message')}")

                    except json.JSONDecodeError:
                        print(f"[WS Warning] Received non-JSON message: {message[:100]}...")
                    except Exception as e:
                        print(f"[WS Error] Error processing message: {e}")

        except websockets.exceptions.ConnectionClosed:
            print("[WS Error] Connection closed. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[WS Error] An error occurred during connection/loop: {e}. Reconnecting in 10 seconds...")
            await asyncio.sleep(10)

# Step 5: Running the Script
if __name__ == '__main__':
    print("Starting Combined PumpPortal Monitor and Logger...")
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Exiting...") 