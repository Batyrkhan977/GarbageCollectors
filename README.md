# Garbage Collectors — Competition v3

A classroom WebSocket competition on a 20×20 map. Teacher controls are protected by `ADMIN_PASSWORD`; student messages never contain wall coordinates or map contents.

## Deploy to your existing Render service

1. Extract this ZIP, then replace the files in your existing GitHub repository with the contents of this folder. Commit to the branch Render deploys.
2. In Render, set the `ADMIN_PASSWORD` environment variable to a **long, unique password** (if not already set). Keep it private; do not add it to GitHub.
3. Deploy the latest commit, then visit:
   - Game and leaderboard: `https://garbage-collectors-aox7.onrender.com/`
   - Teacher manager: `https://garbage-collectors-aox7.onrender.com/admin`
   - Student WebSocket: `wss://garbage-collectors-aox7.onrender.com/play`
   - Public spectator WebSocket: `wss://garbage-collectors-aox7.onrender.com/watch`
4. Open the manager, sign in, **pause**, choose a mode, edit settings and coordinates, **Save settings and reset round**, let up to four students connect, then **Start**.

The free Render service can sleep and restart. Settings, tokens, results, and scores are **in memory only**, so they are lost on restart.

## Server controls and limits

- **Tick rate:** integer 1–50 ticks/second.
- **Maximum garbage:** integer 1–50. The map's initial number of `2` cells must **not exceed** this maximum. To set maximum to 5 with the supplied map (32 initial pieces), edit the map and reduce the number of `2` cells first.
- **Garbage spawn interval:** 0.2–120 seconds; applies only to FFA and 2v2.
- **Built-in random bots:** integer 0–4. Each counts against the **four simultaneous player** cap. For four student competitors, set random bots to **0**.
- **Start positions:** four distinct `[x,y]` pairs on empty `0` cells, each x/y from 0–19. Slot 1 is assigned to player #1, slot 2 to player #2, etc.
- New entrant gets the **lowest unused number** in 1–4. When a bot disconnects, its number is released immediately; score remains in the leaderboard as an offline entry. A reconnect token permits reclaiming its prior record, with whatever number is now available. The server also defensively repairs duplicate active player numbers.
- Saving settings and resetting closes all player connections, clears all scores, and starts a fresh paused round; students reconnect. Joining is only allowed **before the round starts**.

## Game modes

### Free for All (`ffa`)
All connected players compete concurrently. Every participant's collected garbage counts individually. At the manager-configured **duration_seconds**, the game stops and scores freeze. Winner is the greatest score, with lower idle-tick count as tiebreaker. Garbage can respawn until the configured cap.

### 2v2 (`2v2`)
**Exactly four players** must connect before start. Slots **1 and 3 are red**, **2 and 4 are blue**. Every player's garbage goes toward their team's total. Highest team total after the configured round duration wins; equal totals are a draw. Individual scores and idle ticks remain visible. Garbage respawns as in FFA.

### Time Attack (`time_attack`)
Connected players are randomly ordered when the manager starts. Players **take turns**, never move concurrently. At each turn, garbage resets to the same original `2` cells, the player starts at the configured position for their slot, and **no garbage respawns**. When the player collects every piece, `finish_ticks` records elapsed ticks for that turn. Ranking sorts completed runs from shortest to longest; unfinished or skipped runs follow. Run time in seconds is `finish_ticks / ticks_per_second`. It has **no fixed time limit**; the manager may click **Skip current Time Attack run** for a stuck bot. A disconnected active bot is automatically marked disconnected and skipped. Fairness note: starting positions may differ by player slot; for a strict benchmarking event, use layouts with comparably difficult starting cells.

## WebSocket protocol

Students may code bots in **any language** supporting WebSockets (e.g. Python, JavaScript, Java, C++). The client must send:

```json
{"type":"join","role":"bot","name":"MyRobot"}
```

On success the server replies:

```json
{"type":"welcome","player_id":1,"uid":"unique-entrant-id","name":"MyRobot","resume_token":"PRIVATE-SECRET","width":20,"height":20,"tick_ms":200}
```

Save the `resume_token` privately to reconnect to an idle round without losing your bot's score. Do **not** publish it. Another player may take your old number while you are disconnected; read the new `player_id` from `welcome`.

Server broadcasts a `state` message with `tick`, `running`, `finished`, `mode`, `players` (positions and scores), `garbage` (coordinates), `leaderboard`, `teams`, `remaining_seconds`, and Time Attack fields `active_turn`, `trial_ticks`, and `turn_order`. **There is no wall list or map in public state.**

Each game tick, bot can send a movement intention:

```json
{"type":"move","direction":"right"}
```

Allowed directions: `up`, `down`, `left`, `right`, `none`. The server processes the latest received move on its next tick. Sending no direction, or explicitly sending `none`, counts as an idle tick. A directional move blocked by a wall, another player, or a boundary **does not count as idle**. In Time Attack, only the player with `uid == active_turn` can move; other bots should refrain from sending moves until their turn.

Use `student_bot.py` as a minimal Python template. Run:

```bash
pip install -r requirements.txt
python student_bot.py wss://garbage-collectors-aox7.onrender.com/play MyRobot
```

## Map editor preview

Manager's **live** board overlays all currently visible players and current garbage on top of walls. Clicking **Preview map** uses the edited wall layout and current players; if the map text has changed, starting garbage is taken from its `2` characters instead of the old live garbage set. Start slot markers are displayed when a new round is paused, even if a slot has not yet been occupied.

Map text: 20 lines of 20 characters, `0` = floor, `1` = wall, `2` = initial garbage. Edit and save only when paused. Initial positions must be on `0` cells.

## Tests

```bash
python -m unittest -v test_competition
```

Tests cover 1–50 settings, unique IDs, reconnection, four-player capacity, idle-count semantics, timed FFA, 2v2, and Time Attack resets.

## Notes

- A student bot can see player positions and public garbage coordinates, but never wall data. Inferred walls are the student's responsibility.
- Browser spectators connect through `/watch`; the web game page also offers interactive manual play.
- The authenticated `/admin-ws` route requires the teacher's password; public clients cannot issue control commands.
- The service does not yet provide durable historical results or fairness protections against malicious bot traffic, so use it with a trusted classroom group.

## v3: spawn fix and player colors

- FFA and 2v2 now use a single integer tick deadline for spawning; at most **one** piece is added per configured interval, and the maximum garbage count is enforced. Time Attack still disables spawning. Note that collection and spawn may happen in the same tick: the total can remain unchanged while the original garbage is replaced elsewhere.
- Every connected participant is assigned a random distinct color from a palette of 20. FFA and Time Attack use it for the board and name in both leaderboards. 2v2 overrides it with team red/blue. Colors stay consistent across the round and when resuming the same bot.
- Active players appear as `Name (Player 1)` through `Name (Player 4)`; disconnected entries with retained results appear as `Name (Former Player 1)` because their released slot can be reused by another player.
- `color` is available in the public `players` and `leaderboard` objects, and `last_id` is available in the leaderboard. Student protocols remain backward compatible.
