# Garbage Collectors

A small multiplayer game server built with Python `asyncio` and WebSockets. The server is authoritative: clients request moves, but only the server updates positions, checks walls, awards points, and removes collected garbage.

## Quick start

Requires Python 3.10+.

```bash
pip install -r requirements.txt
python server.py
```

The terminal displays the 20×20 map using `#` (wall), `G` (garbage), `.` (empty tile), and digits (players). The default server address is `ws://0.0.0.0:8765` (**bind address**, not a destination to type on other devices).

For another device on the same Wi-Fi network, connect to `ws://SERVER_LOCAL_IP:8765`, replacing `SERVER_LOCAL_IP` with the host computer's LAN IPv4 address. If connections fail, check the host firewall. This prototype uses unencrypted `ws://` and has no authentication; use it on a trusted local network, not directly exposed to the public internet.

Test connection (from the server machine or another device):

```bash
python test_client.py ws://localhost:8765
```

Use `w`, `a`, `s`, `d` plus Enter in the example client. This is a basic testing client, *not* a graphical interface and not a precise 5 Hz input controller.

Run a different map or port:

```bash
python server.py --map my_map.txt --port 9000
```

## Map format

`map.txt` is exactly **20 lines**, each exactly **20 characters**, with no spaces:

- `0`: empty floor
- `1`: impassable wall
- `2`: garbage to collect

Coordinates are zero-indexed: `(x=0, y=0)` is the top-left corner; x increases right, y increases down. A player spawns at a random empty (`0`) tile, never on garbage, a wall, or another player.

## WebSocket protocol (JSON)

Client connects to `ws://HOST:8765`.

**Server → newly connected client** (`welcome`, once):

```json
{"type":"welcome","player_id":1,"width":20,"height":20,"tick_ms":200}
```

**The server never sends wall coordinates.** Clients discover blocked map cells by comparing requested moves with their actual positions in subsequent state messages.

**Client → server** (at most one movement intent per tick recommended):

```json
{"type":"move","direction":"right"}
```

Valid directions: `up`, `down`, `left`, `right`, `none`. If multiple messages arrive before the next tick, **only the latest** is applied. If none arrive, the player stays still. Messages are intents, never absolute positions.

**Server → all clients every 200 milliseconds** (`state`):

```json
{
  "type":"state",
  "tick":35,
  "players":[{"id":1,"x":8,"y":7,"score":3},{"id":2,"x":12,"y":9,"score":1}],
  "garbage":[[3,4],[10,11]],
  "remaining":2
}
```

Each client finds its own entry in `players` by comparing `id` to its `player_id`. The wall layout remains hidden on the server and is never included in WebSocket messages. The full remaining garbage locations are included in every state message. Players cannot walk outside the map, through walls, or onto a tile occupied by another player. Players receive one point per collected garbage tile; removed garbage cannot be collected again. Garbage does not respawn. Disconnecting removes the player from future states; scores are not persistent.

**Error response** (malformed input):

```json
{"type":"error","message":"Invalid direction"}
```

## Game loop

At 5 Hz, the server applies up to one pending move per player, performs collision and collection checks, updates scores, renders a terminal map when anything changes, and broadcasts the authoritative state. Clients should draw their screen from server state, not assume a requested move succeeded. To infer a wall, a client can compare its previous position and chosen direction with its position after the tick. A failed move might also mean the map boundary was reached, another player occupied the target cell, or the input was not applied before that tick; check the tick number and visible player positions before marking a cell as a wall.

## Files

- `server.py` — server, map validation, game logic, rendering, WebSocket handling
- `map.txt` — editable starter map
- `requirements.txt` — Python dependency
- `test_client.py` — minimal client for checking connection and movement


## Hosting online from an iPad (Render)

Upload the **contents** of the `GarbageCollectors` folder to the root of a GitHub repository.
Sign in to Render, select **New > Blueprint**, connect that repository and deploy
using the included `render.yaml`. Alternatively create a Python Web Service with:

- Build command: `pip install -r requirements.txt`
- Start command: `python server.py --no-display`
- Instance type: Free

Render sets the `PORT` environment variable, which `server.py` reads automatically.
The WebSocket URL for remote clients is `wss://YOUR-SERVICE.onrender.com`
(no `:8765` in the URL). No wall locations are sent to clients.
Free services may sleep after 15 minutes with no incoming traffic and can restart,
resetting the in-memory game. Active incoming WebSocket moves normally prevent idle sleep.
The game currently has no access control; anyone with the address can join.
