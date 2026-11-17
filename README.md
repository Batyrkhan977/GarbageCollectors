# Garbage Collectors — Classroom Bot Competition

A Python WebSocket server + two browser pages + student bot SDK/example. **Walls never leave the teacher's authenticated admin connection.**

## Existing Render deployment

1. Replace `server.py`, `index.html`, `requirements.txt`, and `render.yaml` in your existing `GarbageCollectors` GitHub repository. Add `admin.html`, `student_bot.py`, and `.gitignore`. Keep `map.txt` (or replace it with the included sample). Commit to `main`.
2. In **Render → garbage-collectors → Environment**, add an environment variable `ADMIN_PASSWORD` with a **long, unique password** (not in GitHub). Save it; Render should redeploy (or use **Manual Deploy → Deploy latest commit**).
3. Teacher page: `https://garbage-collectors-aox7.onrender.com/admin`. Student spectator/player page: `https://garbage-collectors-aox7.onrender.com/`.
4. Teacher signs in, optionally changes settings / 20×20 map, clicks **Start / Resume**. Reset or Save settings starts a new paused round and disconnects old participants.
5. Students write a bot with the protocol below, each using a **unique bot name**.

**Security:** Serve through Render HTTPS/WSS. Never share `ADMIN_PASSWORD`, and don't put it into a client or a public repository. Anyone who knows the public game endpoint can join as a player; the teacher password only protects management. For a private class, add player access codes later. The admin login page itself is public, but game control requires the manager password.

**Persistence:** Scores, map edits, and settings reside in memory and **reset on Render service restart / sleep**. For long-running competitions, use an always-on service and a database; Render's free tier can sleep when inactive. The file `map.txt` is the durable default map; admin edits are ephemeral.

## Local start

```bash
pip install -r requirements.txt
export ADMIN_PASSWORD='choose-a-long-secret'    # PowerShell: $env:ADMIN_PASSWORD='choose-a-long-secret'
python server.py
```

Open `http://localhost:8765/` and `http://localhost:8765/admin`. The browser client uses `ws://` locally and `wss://` on Render. Do not expose unencrypted local admin logins on the internet.

## Student WebSocket protocol

Endpoint: `wss://garbage-collectors-aox7.onrender.com/play`.

On opening the WebSocket, send within 10 seconds:

```json
{"type":"join","role":"bot","name":"AliceBot"}
```

Bot names must be unique (case-insensitive), 1–24 characters. The server responds:

```json
{"type":"welcome","player_id":1,"name":"AliceBot","resume_token":"SECRET_TOKEN","width":20,"height":20,"tick_ms":200}
```

Store your **private** `resume_token` locally. To rejoin the same round without losing your score, send:

```json
{"type":"join","role":"bot","name":"AliceBot","resume_token":"SECRET_TOKEN"}
```

During a running game, the server broadcasts a `state` message every tick:

```json
{
  "type":"state", "tick":42, "running":true, "tick_ms":200,
  "width":20, "height":20,
  "players":[{"id":1,"name":"AliceBot","x":5,"y":9,"score":4,"idle_ticks":2,"is_bot":true,"kind":"student","connected":true}],
  "leaderboard":[{"id":1,"name":"AliceBot","score":4,"idle_ticks":2,"kind":"student","connected":true}],
  "garbage":[[3,8],[11,5]], "remaining":2
}
```

**No `walls` field is sent**. Garbage is broadcast globally. Coordinates use `(x,y)`, with `(0,0)` at top left, x increasing to the right, y downward.

Send one move for each upcoming tick:

```json
{"type":"move","direction":"right"}
```

Directions: `up`, `down`, `left`, `right`, `none`. You can send multiple moves in a tick, but only the most recent counts. A move received too late may apply to the following tick. After the server advances, it sends the updated state.

`idle_ticks` increments when a player sends `none` **or sends no move** for that tick. A directional move attempting to enter a wall, boundary, or occupied square is **not** counted idle. The random built-in bots always attempt a direction. Disconnected student bots remain visible on the leaderboard but do not accumulate idle ticks during disconnection.

Students **must not** send `ADMIN_PASSWORD` or connect to `/admin-ws`.

## Classroom competition rules

- Teacher can start, pause, reset and update map, tick rate (1–20/s), garbage spawning frequency (0.2–120s), garbage limit (1–300), and built-in random bot count (0–10).
- Teacher's manager page `/admin` shows the entire map; students' main page `/` and `/watch` receive no walls.
- Map file is exactly 20 rows of 20 characters: `0` empty, `1` wall, `2` initial garbage. Teacher may edit map in their dashboard when paused. Saving settings **resets the round**.
- Random bots are competitors with names `Random Bot 1` and `Random Bot 2`; student bots are identified by their unique names.
- Leaderboard sorts by garbage collected descending, then fewer idle ticks (tie breaker). Scores remain after student disconnects and reset for a new round.
- Each tick resolves movement in rotating priority order to reduce first-player advantage. No two players share a cell. Garbage appears at an empty floor cell once per configured interval when below the cap.

## Start an example student bot

```bash
python student_bot.py wss://garbage-collectors-aox7.onrender.com/play "My First Bot"
```

Edit `choose_move(state, my_id)` in `student_bot.py` to implement intelligent map exploration and collection. It begins with random movement. Python requires the dependency in `requirements.txt`; students can also implement the same JSON messages in JavaScript or another WebSocket-capable language. The bot's reconnect token is stored in a local file which `.gitignore` excludes from commits. A new round invalidates old tokens and the example bot automatically retries with a fresh identity.
