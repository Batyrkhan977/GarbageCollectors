"""Garbage Collectors: authoritative multiplayer WebSocket server.

Run: python server.py --map map.txt --host 0.0.0.0 --port 8765
Protocol details are in README.md.
"""

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response
from websockets.exceptions import ConnectionClosed

WIDTH = 20
HEIGHT = 20
TICK_SECONDS = 0.2
BOT_COUNT = 2
MAX_GARBAGE = 32
GARBAGE_SPAWN_TICKS = 10  # 10 * 0.2 seconds = 2 seconds
DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
    "none": (0, 0),
}

logger = logging.getLogger("garbage_collectors")


@dataclass
class Player:
    id: int
    x: int
    y: int
    score: int = 0
    pending_move: str = "none"
    is_bot: bool = False


class Game:
    def __init__(self, map_path: Path, *, display: bool = True):
        self.walls, self.garbage = self.load_map(map_path)
        self.players: dict[ServerConnection, Player] = {}
        self.bots: list[Player] = []
        self.next_id = 1
        self.tick_number = 0
        self.display = display
        self.changed = True
        self.rng = random.Random()
        if len(self.garbage) > MAX_GARBAGE:
            raise ValueError(f"Map has more than {MAX_GARBAGE} pieces of garbage")
        for index in range(BOT_COUNT):
            x, y = self.spawn()
            self.bots.append(Player(id=-(index + 1), x=x, y=y, is_bot=True))

    @staticmethod
    def load_map(path: Path) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) != HEIGHT:
            raise ValueError(f"Map must contain exactly {HEIGHT} lines, found {len(lines)}")
        walls: set[tuple[int, int]] = set()
        garbage: set[tuple[int, int]] = set()
        for y, line in enumerate(lines):
            if len(line) != WIDTH or any(c not in "012" for c in line):
                raise ValueError(
                    f"Map line {y + 1} must contain exactly {WIDTH} characters (0, 1, or 2)"
                )
            for x, value in enumerate(line):
                if value == "1":
                    walls.add((x, y))
                elif value == "2":
                    garbage.add((x, y))
        return walls, garbage

    def all_players(self) -> list[Player]:
        return [*self.players.values(), *self.bots]

    def spawn(self) -> tuple[int, int]:
        occupied = {(p.x, p.y) for p in self.all_players()}
        # Players never spawn on a wall, garbage, or another player.
        choices = [
            (x, y)
            for y in range(HEIGHT)
            for x in range(WIDTH)
            if (x, y) not in self.walls
            and (x, y) not in self.garbage
            and (x, y) not in occupied
        ]
        if not choices:
            raise RuntimeError("No free spawn locations")
        return self.rng.choice(choices)

    def welcome(self, player: Player) -> dict[str, Any]:
        return {
            "type": "welcome",
            "player_id": player.id,
            "width": WIDTH,
            "height": HEIGHT,
            "tick_ms": int(TICK_SECONDS * 1000),
        }

    def state(self) -> dict[str, Any]:
        return {
            "type": "state",
            "tick": self.tick_number,
            "players": [
                {"id": p.id, "x": p.x, "y": p.y, "score": p.score, "is_bot": p.is_bot}
                for p in sorted(self.all_players(), key=lambda p: p.id)
            ],
            "garbage": [
                [x, y] for x, y in sorted(self.garbage, key=lambda p: (p[1], p[0]))
            ],
            "remaining": len(self.garbage),
        }

    def advance(self) -> None:
        # Each tick, humans use their latest queued move, while bots pick a random
        # cardinal direction (including occasionally walking into a wall).
        self.tick_number += 1
        everyone = self.all_players()
        occupied = {(p.x, p.y) for p in everyone}
        for bot in self.bots:
            bot.pending_move = self.rng.choice(("up", "down", "left", "right"))

        # Rotate priority to prevent one player always winning contested squares.
        if everyone:
            offset = self.tick_number % len(everyone)
            everyone = everyone[offset:] + everyone[:offset]
        for player in everyone:
            direction = player.pending_move
            player.pending_move = "none"
            dx, dy = DIRECTIONS[direction]
            nx, ny = player.x + dx, player.y + dy
            target = (nx, ny)
            if dx == dy == 0 or not (0 <= nx < WIDTH and 0 <= ny < HEIGHT):
                continue
            if target in self.walls or target in occupied:
                continue
            occupied.remove((player.x, player.y))
            occupied.add(target)
            player.x, player.y = target
            self.changed = True
            if target in self.garbage:
                self.garbage.remove(target)
                player.score += 1
                logger.info("%s %s collected garbage at (%s, %s)",
                            "Bot" if player.is_bot else "Player", player.id, nx, ny)

        # Every 2 seconds, add exactly one piece on unoccupied floor, until
        # there are 32 pieces. Neither a player nor a bot can be underneath it.
        if self.tick_number % GARBAGE_SPAWN_TICKS == 0 and len(self.garbage) < MAX_GARBAGE:
            free_cells = [
                (x, y)
                for y in range(HEIGHT)
                for x in range(WIDTH)
                if (x, y) not in self.walls
                and (x, y) not in self.garbage
                and (x, y) not in occupied
            ]
            if free_cells:
                location = self.rng.choice(free_cells)
                self.garbage.add(location)
                self.changed = True
                logger.info("New garbage spawned at %s (total: %s)",
                            location, len(self.garbage))

    def render(self) -> None:
        if not self.display or not self.changed:
            return
        grid = [["." for _ in range(WIDTH)] for _ in range(HEIGHT)]
        for x, y in self.walls:
            grid[y][x] = "#"
        for x, y in self.garbage:
            grid[y][x] = "G"
        for player in self.all_players():
            grid[player.y][player.x] = "B" if player.is_bot else str(player.id % 10)
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")
        print(f"Garbage Collectors | tick {self.tick_number} | garbage left: {len(self.garbage)}")
        print("Legend: # wall, G garbage, . floor, B bot, digits player IDs (mod 10)")
        print("\n".join("".join(row) for row in grid))
        scores = ", ".join(f"{'B' if p.is_bot else 'P'}{abs(p.id)}: {p.score}" for p in sorted(self.all_players(), key=lambda p: p.id))
        print("Scores: " + (scores or "no players"), flush=True)
        self.changed = False

    async def handle_client(self, websocket: ServerConnection) -> None:
        try:
            x, y = self.spawn()
        except RuntimeError:
            await websocket.close(code=1013, reason="Map is full")
            return
        player = Player(id=self.next_id, x=x, y=y)
        self.next_id += 1
        self.players[websocket] = player
        self.changed = True
        logger.info("Player %s connected at (%s, %s)", player.id, x, y)
        try:
            await websocket.send(json.dumps(self.welcome(player)))
            await websocket.send(json.dumps(self.state()))
            async for raw_message in websocket:
                try:
                    message = json.loads(raw_message)
                except (json.JSONDecodeError, TypeError):
                    await websocket.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue
                if not isinstance(message, dict) or message.get("type") != "move":
                    await websocket.send(json.dumps({"type": "error", "message": "Expected a move message"}))
                    continue
                direction = message.get("direction")
                if direction not in DIRECTIONS:
                    await websocket.send(json.dumps({"type": "error", "message": "Invalid direction"}))
                    continue
                # Multiple inputs within a tick: only the latest is applied.
                player.pending_move = direction
        except ConnectionClosed:
            pass
        finally:
            self.players.pop(websocket, None)
            self.changed = True
            logger.info("Player %s disconnected", player.id)

    async def tick_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            next_tick += TICK_SECONDS
            await asyncio.sleep(max(0, next_tick - loop.time()))
            self.advance()
            self.render()
            if self.players:
                payload = json.dumps(self.state())
                # Sending sequentially with bounded writes avoids concurrent state mutations.
                # The server library times out stalled connections via ping_timeout.
                await asyncio.gather(
                    *(ws.send(payload) for ws in list(self.players)),
                    return_exceptions=True,
                )


def serve_browser_page(connection: ServerConnection, request: Request) -> Response | None:
    """Return HTML to ordinary HTTP visitors, leaving WebSocket upgrades untouched."""
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    if request.path.split("?", 1)[0] == "/":
        response = connection.respond(200, Path(__file__).with_name("index.html").read_text(encoding="utf-8"))
        del response.headers["Content-Type"]
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Cache-Control"] = "no-store"
        return response
    if request.path.split("?", 1)[0] == "/health":
        response = connection.respond(200, "ok\n")
        del response.headers["Content-Type"]
        response.headers["Content-Type"] = "text/plain"
        return response
    return connection.respond(404, "Not found\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Garbage Collectors multiplayer server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--map", type=Path, default=Path(__file__).with_name("map.txt"))
    parser.add_argument("--no-display", action="store_true", help="Do not print the live terminal map")
    args = parser.parse_args()

    game = Game(args.map, display=not args.no_display)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Loaded %s walls and %s garbage tiles", len(game.walls), len(game.garbage))
    async with serve(game.handle_client, args.host, args.port, max_size=4096, process_request=serve_browser_page):
        logger.info("Listening on ws://%s:%s", args.host, args.port)
        game.render()
        await game.tick_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")
