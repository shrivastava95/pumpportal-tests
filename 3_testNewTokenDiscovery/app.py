import asyncio
import json
import os
import time
import threading
import websockets
import requests # Keep requests for potential future use (e.g., fetching extra data)
from flask import Flask, render_template_string, Response
from flask_sock import Sock

# --- Configuration ---
PUMPORTAL_URI = "wss://pumpportal.fun/api/data"

# --- Flask Setup ---
app = Flask(__name__)
sock = Sock(app)

# --- Shared State ---
# Store the latest new token events (maybe limit the size later if needed)
new_token_events = []
new_token_lock = threading.Lock()

# --- Background Task ---

async def pumpportal_new_token_monitor():
    """Monitors PumpPortal WebSocket for new token creation events."""
    global new_token_events
    loop = asyncio.get_event_loop()

    while True:
        try:
            async with websockets.connect(PUMPORTAL_URI, open_timeout=15, close_timeout=10) as websocket:
                print("[WS Monitor] WebSocket Connected.")
                # Subscribe to new token events
                payload = {"method": "subscribeNewToken"}
                await websocket.send(json.dumps(payload))
                print("[WS Monitor] Subscribed to new token events. Waiting for data...")

                async for message in websocket:
                    try:
                        token_info = json.loads(message)
                        # Add timestamp to the event data
                        token_info['received_at'] = time.time()

                        print(f"[WS Monitor] Received new token event: {token_info.get('mint', 'N/A')}")

                        # Store the event (append to list)
                        with new_token_lock:
                             # Add to the beginning so newest appear first
                            new_token_events.insert(0, token_info) 
                            # Optional: Limit the size of the list
                            # if len(new_token_events) > 100: # Keep last 100 events
                            #    new_token_events.pop() 

                    except json.JSONDecodeError:
                        print("[WS Monitor] Received non-JSON message:", message)
                    except Exception as e:
                        print(f"[WS Monitor Error] Error processing message: {e}")

        except websockets.exceptions.ConnectionClosed:
            print("[WS Monitor] Connection closed. Reconnecting in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
             print(f"[WS Monitor Error] Connection failed: {e}. Retrying in 10s...")
             await asyncio.sleep(10) # Wait before retrying connection

# --- Flask Routes ---

@app.route('/')
def index():
    """Renders the main HTML page for the new token monitor."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>PumpPortal New Token Monitor</title>
        <style>
            body { font-family: sans-serif; margin: 20px; background-color: #f4f4f4; }
            h1 { color: #333; }
            #status { margin-bottom: 15px; font-style: italic; color: #666; font-size: 0.9em; }
            #tokens {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
                gap: 15px;
            }
            .token-card {
                background-color: #e7f5ff; /* Light blue background for new tokens */
                border: 1px solid #b3d7f0;
                border-radius: 5px;
                padding: 15px;
                box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
                word-wrap: break-word; /* Ensure long strings wrap */
            }
            .token-card h3 { margin-top: 0; font-size: 1.1em; word-wrap: break-word; }
            .token-card p { margin: 5px 0; font-size: 0.9em; }
            .label { font-weight: bold; min-width: 120px; display: inline-block; }
            .value { color: #555; }
            .timestamp { font-size: 0.8em; color: #777; margin-top: 10px; }
            pre { 
                white-space: pre-wrap; /* Allow wrapping */
                word-wrap: break-word; /* Break long words/strings */
                background-color: #eee; 
                padding: 5px; 
                border-radius: 3px; 
                font-size: 0.85em; 
                max-height: 150px; /* Limit height */
                overflow-y: auto; /* Add scrollbar if needed */
            } 
        </style>
    </head>
    <body>
        <h1>PumpPortal New Token Monitor</h1>
        <div id="status">Connecting to stream...</div>
        <div id="tokens">Waiting for new token events...</div>

        <script>
            const tokensDiv = document.getElementById('tokens');
            const statusDiv = document.getElementById('status');
            const eventSource = new EventSource('/stream');
            let eventCount = 0;

            // Compacted timeSince function (same as before)
            function timeSince(timestamp) {
                if (!timestamp) return 'N/A';
                const now = Date.now();
                const secondsPast = Math.floor((now - timestamp * 1000) / 1000);
                if (secondsPast < 1) return 'just now';
                if (secondsPast < 60) return secondsPast + 's ago';
                const minutesPast = Math.floor(secondsPast / 60);
                if (minutesPast < 60) return minutesPast + 'm ago';
                const hoursPast = Math.floor(minutesPast / 60);
                if (hoursPast < 24) return hoursPast + 'h ago';
                const daysPast = Math.floor(hoursPast / 24);
                return daysPast + 'd ago';
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

            eventSource.onmessage = function(event) {
                const newEvents = JSON.parse(event.data);
                statusDiv.textContent = `Connected. Received ${newEvents.length} total events.`;

                if (newEvents.length === 0 && eventCount === 0) {
                    tokensDiv.innerHTML = '<p>Waiting for the first new token event...</p>';
                    return;
                } else if (newEvents.length > 0 && eventCount === 0) {
                     tokensDiv.innerHTML = ''; // Clear initial message
                }
                
                eventCount = newEvents.length; // Update total count

                let html = '';
                // Process events (they are already sorted newest first from backend)
                for (const tokenData of newEvents) {
                    const mint = tokenData.mint || 'N/A';
                    const name = tokenData.name || 'N/A';
                    const symbol = tokenData.symbol || 'N/A';
                    const receivedAt = tokenData.received_at || null;
                    const timeAgo = timeSince(receivedAt);
                    const cardId = `card-${mint}-${receivedAt}`; // Unique ID
                    
                    // Basic display - adapt as needed based on actual event structure
                    html += `
                        <div class="token-card" id="${cardId}">
                            <h3>${name} (${symbol})</h3>
                            <p><span class="label">Mint Address:</span> <span class="value">${mint}</span></p>
                            <p><span class="label">Received:</span> <span class="value time-ago-value" data-timestamp="${receivedAt}">${timeAgo}</span></p>
                            <p><span class="label">Raw Data:</span></p>
                            <pre>${JSON.stringify(tokenData, null, 2)}</pre>
                        </div>
                    `;
                }
                tokensDiv.innerHTML = html;
            };

            // Start the periodic timer to update time displays
            setInterval(updateTimeDisplays, 5000); // Update every 5 seconds

            eventSource.onerror = function(err) {
                console.error("EventSource failed:", err);
                tokensDiv.innerHTML = '<p style="color: red;">Error connecting to data stream. Please refresh.</p>';
                statusDiv.textContent = 'Error connecting to data stream.';
                eventSource.close();
            };
        </script>
    </body>
    </html>
    """
    return render_template_string(html_content)

@app.route('/stream')
def stream():
    """Provides a Server-Sent Events (SSE) stream of new token events."""
    def event_stream():
        last_event_count = -1
        while True:
            with new_token_lock:
                current_event_count = len(new_token_events)
                if current_event_count != last_event_count:
                    # Send the entire list (newest first)
                    payload_json = json.dumps(list(new_token_events)) 
                    yield f"data: {payload_json}\n\n"
                    last_event_count = current_event_count
            # Check less frequently than the other app, maybe every 2 seconds
            time.sleep(2) 
    return Response(event_stream(), mimetype='text/event-stream')

# --- Startup ---

def run_async_tasks():
    """Runs the background async task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(pumpportal_new_token_monitor())

if __name__ == '__main__':
    print("Starting PumpPortal New Token Monitor Web App...")
    monitor_thread = threading.Thread(target=run_async_tasks, daemon=True)
    monitor_thread.start()
    # Make sure to use a different port than the other app
    app.run(host='0.0.0.0', port=5002, debug=False, use_reloader=False) 