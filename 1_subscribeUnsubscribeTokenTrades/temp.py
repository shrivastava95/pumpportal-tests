import asyncio
import json
import websockets
import os

TOKEN_FILE = "tokens.txt"
POLL_INTERVAL = 5  # seconds

async def read_tokens():
    if not os.path.exists(TOKEN_FILE):
        return set()
    with open(TOKEN_FILE, 'r') as f:
        return set(line.strip() for line in f if line.strip())

async def monitor_tokens(websocket, subscribed_tokens):
    while True:
        current_tokens = await read_tokens()
        new_tokens = current_tokens - subscribed_tokens
        removed_tokens = subscribed_tokens - current_tokens

        for token in new_tokens:
            payload = {
                "method": "subscribeTokenTrade",
                "keys": [token]
            }
            await websocket.send(json.dumps(payload))
            print(f"Subscribed to new token: {token}")

        for token in removed_tokens:
            payload = {
                "method": "unsubscribeTokenTrade",
                "keys": [token]
            }
            await websocket.send(json.dumps(payload))
            print(f"Unsubscribed from token: {token}")

        subscribed_tokens = current_tokens
        await asyncio.sleep(POLL_INTERVAL)

async def subscribe():
    uri = "wss://pumpportal.fun/api/data"
    async with websockets.connect(uri) as websocket:
        subscribed_tokens = set()
        token_task = asyncio.create_task(monitor_tokens(websocket, subscribed_tokens))

        async for message in websocket:
            data = json.loads(message)
            print(data)

# Start it
asyncio.run(subscribe())

