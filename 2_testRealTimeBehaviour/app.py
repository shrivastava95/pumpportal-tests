import asyncio
import json
import os
import time
import threading
import websockets
import requests
from flask import Flask, render_template_string, Response
from flask_sock import Sock

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
# TOKEN_FILE = os.path.join(SCRIPT_DIR, "tokens.txt") # Removed
POLL_INTERVAL_SECONDS = 5
PUMPORTAL_URI = "wss://pumpportal.fun/api/data"
COINGECKO_API_URL = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
SOL_PRICE_POLL_INTERVAL_SECONDS = 60
DEXSCREENER_API_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

# --- Flask Setup ---
app = Flask(__name__)
sock = Sock(app)

# --- Shared State ---
token_data_store = {}
active_subscriptions = set()
current_sol_price_usd = None
sol_price_last_updated = 0

# --- Helper Functions ---

def fetch_initial_tokens():
    """Fetches initial token list from DexScreener API."""
    print("[Startup] Fetching initial tokens from DexScreener...")
    try:
        response = requests.get(DEXSCREENER_API_URL, headers={"Accept":"*/*"}, timeout=15)
        response.raise_for_status()
        data = response.json()
        # Filter for Solana tokens and extract addresses
        solana_tokens = { 
            item["tokenAddress"] for item in data 
            if isinstance(item, dict) and item.get("chainId") == "solana" and item.get("tokenAddress")
        }
        if not solana_tokens:
            print("[Startup Error] No Solana tokens found in DexScreener response.")
            return set()
        print(f"[Startup] Fetched {len(solana_tokens)} initial Solana tokens.")
        return solana_tokens
    except requests.exceptions.RequestException as e:
        print(f"[Startup Error] Failed to fetch from DexScreener: {e}")
        return set() # Return empty set on error
    except Exception as e:
        print(f"[Startup Error] Unexpected error fetching DexScreener tokens: {e}")
        return set()

# Removed read_tokens_from_file()

async def update_subscriptions(websocket, desired_tokens, active_tokens):
    """Subscribes/unsubscribes based on token list changes."""
    # This function remains largely the same, but will now only be 
    # effectively called once at startup with the initial token list.
    new_tokens = desired_tokens - active_tokens
    removed_tokens = active_tokens - desired_tokens
    tasks = []
    if new_tokens:
        payload = {"method": "subscribeTokenTrade", "keys": list(new_tokens)}
        tasks.append(websocket.send(json.dumps(payload)))
    if removed_tokens:
        payload = {"method": "unsubscribeTokenTrade", "keys": list(removed_tokens)}
        tasks.append(websocket.send(json.dumps(payload)))
        for token in removed_tokens:
            token_data_store.pop(token, None)
    if tasks:
        await asyncio.gather(*tasks)
        # print("[WS Monitor] Subscription update sent.") # Less noisy log
        return desired_tokens
    return active_tokens

# --- Background Tasks (Minimal Error Handling) ---

async def poll_sol_price():
    """Fetches the SOL price from CoinGecko periodically and updates the global state."""
    global current_sol_price_usd, sol_price_last_updated
    loop = asyncio.get_event_loop()
    while True:
        response = await loop.run_in_executor(None, lambda: requests.get(COINGECKO_API_URL, timeout=10))
        response.raise_for_status() 
        data = response.json()
        price = data.get("solana", {}).get("usd")
        if price is not None:
            current_sol_price_usd = float(price)
            sol_price_last_updated = time.time()
            print(f"[CoinGecko] Updated SOL price: ${current_sol_price_usd}")
        await asyncio.sleep(SOL_PRICE_POLL_INTERVAL_SECONDS)

async def pumpportal_monitor(initial_tokens_to_monitor):
    """Monitors PumpPortal WebSocket for trades for a fixed initial set of tokens."""
    global active_subscriptions # Need global to read the desired state
    loop = asyncio.get_event_loop()
    
    # Set the initial desired tokens (might be updated by add/remove routes)
    # active_subscriptions is already initialized globally and modified by fetch/add/remove
    # desired_tokens = initial_tokens_to_monitor # No longer needed here
    
    while True:
        websocket_subscribed_tokens = set() # Track subscriptions for this connection attempt
        try: # Add outer try/except for connection phase
            async with websockets.connect(PUMPORTAL_URI, open_timeout=15, close_timeout=10) as websocket:
                print("[WS Monitor] WebSocket Connected.")
                
                # Perform initial subscription based on current global state
                print(f"[WS Monitor] Subscribing to initial {len(active_subscriptions)} tokens...")
                websocket_subscribed_tokens = await update_subscriptions(websocket, active_subscriptions.copy(), websocket_subscribed_tokens) # Pass copy to avoid race condition
                print(f"[WS Monitor] Initial subscription sent for {len(websocket_subscribed_tokens)} tokens.")

                # Loop to receive messages AND check for subscription changes
                while True: 
                    try:
                        # Check for subscription changes FIRST (before potentially blocking on recv)
                        current_desired_tokens = active_subscriptions.copy()
                        if current_desired_tokens != websocket_subscribed_tokens:
                            print("[WS Monitor] Detected change in subscriptions. Updating...")
                            websocket_subscribed_tokens = await update_subscriptions(websocket, current_desired_tokens, websocket_subscribed_tokens)
                            print(f"[WS Monitor] Subscription update sent. Now monitoring {len(websocket_subscribed_tokens)} tokens.")

                        # Receive message with timeout (allows periodic check above)
                        message = await asyncio.wait_for(websocket.recv(), timeout=POLL_INTERVAL_SECONDS) 
                        
                        data = json.loads(message)
                        mint = data.get("mint")
                        signature = data.get("signature")
                        if mint and signature: 
                            mc_sol_str = data.get("marketCapSol")
                            usd_price = data.get("usdPrice")
                            tx_type = data.get("txType")
                            
                            market_cap_usd = None
                            if mc_sol_str is not None and current_sol_price_usd is not None:
                                mc_sol = float(mc_sol_str)
                                market_cap_usd = mc_sol * current_sol_price_usd
                            
                            # Update only if it's one of the tokens we *should* be subscribed to
                            if mint in active_subscriptions: 
                                token_data_store[mint] = {
                                    "marketCapUsd": market_cap_usd,
                                    "usdPrice": usd_price,
                                    "lastTxType": tx_type,
                                    "lastUpdate": time.time()
                                }
                    except asyncio.TimeoutError:
                        # Timeout is expected, loop back to check subscriptions and wait again
                        continue 
                    except websockets.exceptions.ConnectionClosed:
                        print("[WS Monitor] Connection closed during loop. Reconnecting...")
                        break # Break inner loop to trigger reconnection
                    # Other exceptions will propagate and likely crash the thread
        except Exception as e:
             print(f"[WS Monitor Error] Connection or initial subscription failed: {e}. Retrying in 10s...")
             # Reset potentially partially modified subscriptions on connection error
             active_subscriptions = active_subscriptions # Ensure global state isn't wrongly modified if update failed
             await asyncio.sleep(10) # Wait before retrying connection

# --- Flask Routes (Unchanged) ---

@app.route('/')
def index():
    """Renders the main HTML page for the token monitor."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>PumpPortal Monitor</title>
        <style>
            body { font-family: sans-serif; margin: 20px; background-color: #f4f4f4; }
            h1 { color: #333; }
            #status { margin-bottom: 15px; font-style: italic; color: #666; font-size: 0.9em; }
            /* --- Styles for Add Token Form --- */
            #add-token-form {
                margin-bottom: 20px;
                background-color: #eee;
                padding: 10px;
                border-radius: 5px;
                display: flex;
                gap: 10px;
                align-items: center;
            }
            #add-token-form input[type="text"] {
                flex-grow: 1;
                padding: 8px;
                border: 1px solid #ccc;
                border-radius: 3px;
            }
            #add-token-form button {
                padding: 8px 15px;
                border: none;
                background-color: #5cb85c;
                color: white;
                border-radius: 3px;
                cursor: pointer;
                transition: background-color 0.2s;
            }
            #add-token-form button:hover {
                background-color: #4cae4c;
            }
            /* ------------------------------ */
            #tokens {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); /* Wider cards */
                gap: 15px;
            }
            .token-card {
                position: relative; /* Needed for absolute positioning of button */
                background-color: #fff; /* Default background */
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 15px;
                box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
                transition: background-color 1s ease-out; /* Apply fade transition to background */
            }
            .token-card h3 { margin-top: 0; font-size: 1.1em; word-wrap: break-word; }
            .token-card p { margin: 5px 0; font-size: 0.9em; }
            .label { font-weight: bold; min-width: 120px; display: inline-block; }
            .value { color: #555; }

            /* --- CSS for Flashing Effect (Simplified) --- */
            .flash-red {
                background-color: #ffcccc; /* Slightly stronger initial red */
                border-color: #ffaaaa;
            }
            .flash-green {
                background-color: #ccffcc; /* Slightly stronger initial green */
                border-color: #aaffaa;
            }
            /* ----------------------------------------- */

            /* --- CSS for Remove Button --- */
            .remove-btn {
                position: absolute;
                top: 5px;
                right: 5px;
                background: #eee;
                border: 1px solid #ccc;
                border-radius: 50%;
                width: 20px;
                height: 20px;
                line-height: 18px; /* Adjust for vertical centering */
                text-align: center;
                font-size: 12px;
                font-weight: bold;
                color: #666;
                cursor: pointer;
                transition: background-color 0.2s, color 0.2s;
            }
            .remove-btn:hover {
                background-color: #f55;
                color: white;
                border-color: #d44;
            }
            /* -------------------------- */
        </style>
    </head>
    <body>
        <h1>PumpPortal Token Monitor</h1>
        <div id="status">SOL Price: Loading...</div>
        
        <!-- Add Token Form -->
        <div id="add-token-form">
            <input type="text" id="new-token-input" placeholder="Enter Solana Token Address...">
            <button id="add-token-btn">Add Token</button>
        </div>
        
        <div id="tokens">Loading token data...</div>

        <script>
            const tokensDiv = document.getElementById('tokens');
            const statusDiv = document.getElementById('status');
            const eventSource = new EventSource('/stream');
            let previousTokenData = {};
            const activeFlashes = new Set();
            const newTokenInput = document.getElementById('new-token-input');
            const addTokenBtn = document.getElementById('add-token-btn');

            // Compacted timeSince function
            function timeSince(timestamp) {
                if (!timestamp) return 'N/A';
                const now = Date.now();
                const secondsPast = Math.floor((now - timestamp * 1000) / 1000);
                if (secondsPast < 60) return secondsPast + ' seconds ago';
                const minutesPast = Math.floor(secondsPast / 60);
                if (minutesPast < 60) return minutesPast + (minutesPast === 1 ? ' minute' : ' minutes') + ' ago';
                const hoursPast = Math.floor(minutesPast / 60);
                if (hoursPast < 24) return hoursPast + (hoursPast === 1 ? ' hour' : ' hours') + ' ago';
                const daysPast = Math.floor(hoursPast / 24);
                if (daysPast < 30) return daysPast + (daysPast === 1 ? ' day' : ' days') + ' ago';
                const monthsPast = Math.floor(daysPast / 30.44);
                if (monthsPast < 12) return monthsPast + (monthsPast === 1 ? ' month' : ' months') + ' ago';
                const yearsPast = Math.floor(monthsPast / 12);
                return yearsPast + (yearsPast === 1 ? ' year' : ' years') + ' ago';
            }

            // Compacted applyFlash function
            function applyFlash(element, flashClass, mint) {
                if (!element || activeFlashes.has(mint)) {
                    return;
                }
                activeFlashes.add(mint);
                element.classList.add(flashClass);
                requestAnimationFrame(() => {
                    setTimeout(() => { 
                        element.classList.remove(flashClass);
                    }, 0);
                });
                setTimeout(() => {
                    activeFlashes.delete(mint);
                }, 1000);
            }
            
            // Function to update all time displays periodically
            function updateTimeDisplays() {
                const timeElements = document.querySelectorAll('.time-ago-value');
                timeElements.forEach(el => {
                    const timestamp = el.dataset.timestamp;
                    if (timestamp) {
                        el.textContent = timeSince(parseFloat(timestamp));
                    }
                });
            }

            // Function to handle remove request
            async function removeTokenRequest(mint) {
                try {
                    const response = await fetch(`/remove-token/${mint}`, {
                        method: 'POST',
                    });
                    const result = await response.json();
                    if (response.ok) {
                        console.log(`Successfully requested removal for ${mint}`);
                    } else {
                        console.error(`Failed to remove ${mint}:`, result.message);
                        alert(`Error removing token: ${result.message}`);
                    }
                } catch (error) {
                    console.error("Error sending remove request:", error);
                    alert("Error sending remove request.");
                }
            }
            
            // Function to handle add request
            async function addTokenRequest(mint) {
                if (!mint || mint.length < 30) { // Basic validation
                    alert("Please enter a valid Solana token address.");
                    return;
                }
                try {
                    const response = await fetch(`/add-token/${mint}`, {
                        method: 'POST',
                    });
                    const result = await response.json();
                    if (response.ok) {
                        console.log(`Successfully requested add for ${mint}: ${result.message}`);
                        newTokenInput.value = ''; // Clear input on success
                    } else {
                        console.error(`Failed to add ${mint}:`, result.message);
                        alert(`Error adding token: ${result.message}`);
                    }
                } catch (error) {
                    console.error("Error sending add request:", error);
                    alert("Error sending add request.");
                }
            }

            eventSource.onmessage = function(event) {
                const update = JSON.parse(event.data);
                
                // Update SOL Price Status (compacted)
                if (update.solPrice !== null && update.solPrice !== undefined) {
                    const priceStr = parseFloat(update.solPrice).toFixed(4);
                    const updateTime = update.solPriceLastUpdate ? new Date(update.solPriceLastUpdate * 1000).toLocaleTimeString() : 'N/A';
                    statusDiv.innerHTML = `SOL Price: <b>$${priceStr}</b> (Updated: ${updateTime})`;
                } else {
                    statusDiv.innerHTML = 'SOL Price: Fetching...';
                }

                // Update Token Cards (compacted)
                const currentTokens = update.tokens || {}; 
                if (Object.keys(currentTokens).length === 0) {
                    tokensDiv.innerHTML = '<p>No tokens being monitored or no data received yet.</p>';
                    previousTokenData = {};
                    return;
                }
                
                let html = '';
                const sortedMints = Object.keys(currentTokens).sort(); 
                
                for (const mint of sortedMints) {
                    const data = currentTokens[mint];
                    const mcUsd = data.marketCapUsd !== null && data.marketCapUsd !== undefined 
                                    ? '$' + parseFloat(data.marketCapUsd).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) 
                                    : 'N/A';
                    const usdPrice = data.usdPrice !== null && data.usdPrice !== undefined 
                                     ? '$' + parseFloat(data.usdPrice).toFixed(8) 
                                     : 'N/A';
                    // Get initial time string, but store timestamp in data attribute
                    const timeAgo = timeSince(data.lastUpdate); 
                    const lastUpdateTimestamp = data.lastUpdate || '';
                    const cardId = `card-${mint}`;

                    html += `
                        <div class="token-card" id="${cardId}">
                            <div class="remove-btn" data-mint="${mint}" title="Remove ${mint}">&times;</div> 
                            <h3>${mint}</h3>
                            <p><span class="label">Market Cap (USD):</span> <span class="value">${mcUsd}</span></p>
                            <p><span class="label">Price / Token (USD):</span> <span class="value">${usdPrice}</span></p>
                            <!-- Add class and data-timestamp to the time value span -->
                            <p><span class="label">Last Update:</span> <span class="value time-ago-value" data-timestamp="${lastUpdateTimestamp}">${timeAgo}</span></p> 
                        </div>
                    `;
                }
                tokensDiv.innerHTML = html;

                // Apply flash effect (compacted)
                for (const mint of sortedMints) {
                    const currentData = currentTokens[mint];
                    const previousData = previousTokenData[mint];
                    if (previousData === undefined || currentData.lastUpdate > previousData.lastUpdate) {
                         const cardElement = document.getElementById(`card-${mint}`);
                         if (currentData.lastTxType === 'buy') {
                            applyFlash(cardElement, 'flash-green', mint);
                         } else if (currentData.lastTxType === 'sell') {
                            applyFlash(cardElement, 'flash-red', mint);
                         }
                    }
                }
                
                previousTokenData = currentTokens;
            };

            // Add single event listener for remove buttons (Event Delegation)
            tokensDiv.addEventListener('click', function(event) {
                if (event.target.classList.contains('remove-btn')) {
                    const mintToRemove = event.target.getAttribute('data-mint');
                    if (mintToRemove) {
                        removeTokenRequest(mintToRemove);
                    }
                }
            });

            // Add event listener for the add token button
            addTokenBtn.addEventListener('click', function() {
                const newTokenMint = newTokenInput.value.trim();
                addTokenRequest(newTokenMint);
            });

            // Start the periodic timer to update time displays
            setInterval(updateTimeDisplays, 1000); // Update every second

            eventSource.onerror = function(err) {
                console.error("EventSource failed:", err);
                tokensDiv.innerHTML = '<p style="color: red;">Error connecting to data stream. Please refresh.</p>';
                statusDiv.innerHTML = 'Error connecting to data stream.';
                eventSource.close();
            };
        </script>
    </body>
    </html>
    """
    return render_template_string(html_content)

@app.route('/stream')
def stream():
    """Provides a Server-Sent Events (SSE) stream of token and SOL price data."""
    def event_stream():
        last_data_sent = None
        while True:
            payload = {
                "tokens": dict(token_data_store),
                "solPrice": current_sol_price_usd,
                "solPriceLastUpdate": sol_price_last_updated
            }
            payload_json = json.dumps(payload)
            if payload_json != last_data_sent:
                yield f"data: {payload_json}\n\n"
                last_data_sent = payload_json
            time.sleep(1) 
    return Response(event_stream(), mimetype='text/event-stream')

# Add route to handle token removal
@app.route('/remove-token/<mint>', methods=['POST'])
def remove_token(mint):
    """API endpoint to remove a token from the monitoring list."""
    global active_subscriptions # Access the global set
    # print(f"[Remove Request] Received request to remove: {mint}")
    removed_data = token_data_store.pop(mint, None)
    active_subscriptions.discard(mint)
    if removed_data or mint not in active_subscriptions:
        # print(f"[Remove Request] Removed {mint} successfully.")
        return {"status": "success", "message": f"Token {mint} removed."}, 200
    else:
        # print(f"[Remove Request] Token {mint} not found.")
        return {"status": "error", "message": f"Token {mint} not found."}, 404

# Add route to handle token addition
@app.route('/add-token/<mint>', methods=['POST'])
def add_token(mint):
    """API endpoint to add a token to the monitoring list."""
    global active_subscriptions # Access the global set
    if not mint or len(mint) < 30:
        # print(f"[Add Request] Invalid token address received: {mint}")
        return {"status": "error", "message": "Invalid token address provided."}, 400
    
    # print(f"[Add Request] Received request to add: {mint}")
    
    if mint not in active_subscriptions:
        active_subscriptions.add(mint)
        # print(f"[Add Request] Added {mint} to subscription list.")
        return {"status": "success", "message": f"Token {mint} added."}, 200
    else:
        # print(f"[Add Request] Token {mint} is already being monitored.")
        return {"status": "info", "message": f"Token {mint} already monitored."}, 200

# --- Startup --- 

def run_async_tasks(initial_token_set):
    """Runs all background async tasks with the initial token set."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(asyncio.gather(
        # Pass the initial tokens to the monitor task
        pumpportal_monitor(initial_token_set),
        poll_sol_price()
    ))

if __name__ == '__main__':
    # Fetch initial tokens synchronously before starting background tasks
    initial_tokens = fetch_initial_tokens()
    
    if not initial_tokens:
        print("Could not fetch initial tokens. Proceeding without initial list.")

    print("Starting PumpPortal Web Monitor...")
    monitor_thread = threading.Thread(
        # Pass fetched tokens to the target function
        target=run_async_tasks, args=(initial_tokens,), 
        daemon=True
    )
    monitor_thread.start()
    app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False) 