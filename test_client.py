"""Minimal text client for testing the WebSocket protocol.

Run: python test_client.py ws://localhost:8765
Enter w/a/s/d to queue a move; blank input sends no movement.
"""
import asyncio
import json
import sys

from websockets.asyncio.client import connect

KEYS = {"w": "up", "a": "left", "s": "down", "d": "right", "": "none"}


async def main() -> None:
    uri = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8765"
    async with connect(uri) as ws:
        async def receiver():
            async for raw in ws:
                data = json.loads(raw)
                if data["type"] == "welcome":
                    print(f"Joined as P{data['player_id']} on a {data['width']}x{data['height']} map")
                elif data["type"] == "state":
                    print(f"Tick {data['tick']}: players={data['players']} remaining={data['remaining']}")
                else:
                    print(data)

        receive_task = asyncio.create_task(receiver())
        try:
            print("Type w/a/s/d + Enter to move, or q to quit")
            while True:
                key = (await asyncio.to_thread(input, "> ")).strip().lower()
                if key == "q":
                    break
                if key not in KEYS:
                    print("Use w/a/s/d (or Enter for no movement)")
                    continue
                await ws.send(json.dumps({"type": "move", "direction": KEYS[key]}))
        finally:
            receive_task.cancel()
            await asyncio.gather(receive_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
