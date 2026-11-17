"""Garbage Collectors classroom competition server (Python 3.10+).

Student bots use /play and a documented JSON/WebSocket protocol. Wall locations
are never included in the public player or spectator messages. Teacher controls
are authenticated on /admin-ws using the ADMIN_PASSWORD environment variable.
"""
import argparse
import asyncio
import hmac
import json
import logging
import os
import random
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

W = H = 20
DIRS = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0), 'none': (0, 0)}
LOG = logging.getLogger('garbage_collectors')
BASE = Path(__file__).resolve().parent


def validate_map(text: str):
    rows = text.strip().splitlines()
    if len(rows) != H or any(len(row) != W or any(c not in '012' for c in row) for row in rows):
        raise ValueError('Map must be 20 lines of exactly 20 characters (0, 1, 2)')
    walls, garbage = set(), set()
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c == '1': walls.add((x, y))
            if c == '2': garbage.add((x, y))
    return '\n'.join(rows), walls, garbage


def name_ok(value):
    return isinstance(value, str) and 1 <= len(value.strip()) <= 24 and not any(ord(c) < 32 for c in value)


@dataclass
class Participant:
    id: int
    name: str
    x: int = -1
    y: int = -1
    score: int = 0
    idle_ticks: int = 0
    pending: str = 'none'
    kind: str = 'student'
    token: str = ''
    connected: bool = False


class Competition:
    def __init__(self, map_path: Path):
        self.map_text, self.walls, initial = validate_map(map_path.read_text(encoding='utf-8'))
        self.initial_garbage = initial
        self.garbage = set(initial)
        self.tps = 5
        self.spawn_seconds = 2.0
        self.max_garbage = max(32, len(initial))
        self.random_bots = 2
        self.running = False
        self.tick = 0
        self.spawn_elapsed = 0.0
        self.next_id = 1
        self.rng = random.Random()
        self.participants: dict[int, Participant] = {}
        self.connections: dict[Any, int] = {}
        self.viewers: set[Any] = set()
        self.admins: set[Any] = set()
        self._create_random_bots()

    def _free(self):
        occupied = {(p.x, p.y) for p in self.participants.values() if p.connected or p.kind == 'random'}
        return [(x, y) for y in range(H) for x in range(W)
                if (x,y) not in self.walls and (x,y) not in self.garbage and (x,y) not in occupied]

    def place(self, p):
        cells = self._free()
        if not cells:
            raise ValueError('There is no empty cell available for a player')
        p.x, p.y = self.rng.choice(cells)

    def _create_random_bots(self):
        for i in range(self.random_bots):
            p = Participant(-(i+1), f'Random Bot {i+1}', kind='random', connected=True)
            self.place(p)
            self.participants[p.id] = p

    def reset(self, *, start=False):
        # Starts a new competition; previous scores, idle counts and identities reset.
        self.running = False
        self.tick = 0
        self.spawn_elapsed = 0
        self.garbage = set(self.initial_garbage)
        self.participants.clear()
        self.next_id = 1
        self._create_random_bots()
        for ws in list(self.connections):
            old_id = self.connections[ws]
            # Existing clients keep their WebSocket, but become fresh entrants.
            new_id = self.next_id
            self.next_id += 1
            p = Participant(new_id, f'Player {new_id}', kind='human', connected=True)
            self.place(p)
            self.participants[p.id] = p
            self.connections[ws] = p.id
        self.running = start

    def join(self, ws, msg):
        role = msg.get('role', 'bot')
        if role not in ('bot', 'human'):
            raise ValueError('role must be bot or human')
        name = msg.get('name', '')
        if not name_ok(name):
            raise ValueError('name must be 1–24 characters without control characters')
        name = name.strip()
        resume_token = msg.get('resume_token')
        # Reconnect: only the holder of the secret token may reclaim identity.
        if role == 'bot' and isinstance(resume_token, str) and resume_token:
            for p in self.participants.values():
                if p.kind == 'student' and hmac.compare_digest(p.token, resume_token):
                    if p.connected:
                        raise ValueError('This bot identity is already connected')
                    if p.name != name:
                        raise ValueError('The saved bot name does not match')
                    p.connected = True
                    p.pending = 'none'
                    self.place(p)
                    self.connections[ws] = p.id
                    return p
            raise ValueError('Invalid resume_token (a new round resets tokens)')
        if any(p.name.casefold() == name.casefold() for p in self.participants.values()):
            raise ValueError('That name is already taken; use your resume_token to reconnect')
        if sum(p.kind == 'student' for p in self.participants.values()) >= 100:
            raise ValueError('Maximum number of student bots reached')
        p = Participant(self.next_id, name, kind='student' if role == 'bot' else 'human',
                        token=secrets.token_urlsafe(32) if role == 'bot' else '', connected=True)
        self.next_id += 1
        self.place(p)
        self.participants[p.id] = p
        self.connections[ws] = p.id
        return p

    def disconnect(self, ws):
        pid = self.connections.pop(ws, None)
        if pid is not None and pid in self.participants:
            p = self.participants[pid]
            p.connected = False
            p.pending = 'none'
            p.x = p.y = -1
            # Scores stay on the leaderboard after student bot disconnects.
            if p.kind == 'human':
                self.participants.pop(pid, None)

    def public_state(self):
        return {'type': 'state', 'tick': self.tick, 'running': self.running,
                'tick_ms': round(1000 / self.tps), 'width': W, 'height': H,
                'players': [{'id': p.id, 'name': p.name, 'x': p.x, 'y': p.y,
                             'score': p.score, 'idle_ticks': p.idle_ticks,
                             'is_bot': p.kind != 'human', 'kind': p.kind, 'connected': p.connected}
                            for p in sorted(self.participants.values(), key=lambda p: p.id)
                            if p.connected],
                'leaderboard': [{'id': p.id, 'name': p.name, 'score': p.score,
                                 'idle_ticks': p.idle_ticks, 'kind': p.kind, 'connected': p.connected}
                                for p in sorted(self.participants.values(),
                                                key=lambda p: (-p.score, p.idle_ticks, p.name.casefold()))],
                'garbage': [list(c) for c in sorted(self.garbage)], 'remaining': len(self.garbage)}

    def admin_state(self):
        return {'type': 'admin_state', 'map': self.map_text, 'tps': self.tps,
                'spawn_seconds': self.spawn_seconds, 'max_garbage': self.max_garbage,
                'random_bots': self.random_bots, **self.public_state(), 'type': 'admin_state'}

    def advance(self):
        if not self.running:
            return
        self.tick += 1
        players = [p for p in self.participants.values() if p.connected]
        occupied = {(p.x, p.y) for p in players}
        for p in players:
            if p.kind == 'random':
                p.pending = self.rng.choice(['up', 'down', 'left', 'right'])
        if players:
            shift = self.tick % len(players)
            players = players[shift:] + players[:shift]
        for p in players:
            direction = p.pending
            p.pending = 'none'
            # Only direction == 'none' is idle. A blocked directional attempt
            # counts as an attempted movement and never increases idle_ticks.
            if direction == 'none':
                p.idle_ticks += 1
                continue
            dx, dy = DIRS[direction]
            nx, ny = p.x + dx, p.y + dy
            dest = (nx, ny)
            if not (0 <= nx < W and 0 <= ny < H) or dest in self.walls or dest in occupied:
                continue
            occupied.discard((p.x, p.y))
            occupied.add(dest)
            p.x, p.y = nx, ny
            if dest in self.garbage:
                self.garbage.remove(dest)
                p.score += 1
        self.spawn_elapsed += 1 / self.tps
        if self.spawn_elapsed + 1e-9 >= self.spawn_seconds:
            self.spawn_elapsed %= self.spawn_seconds
            if len(self.garbage) < self.max_garbage:
                cells = self._free()
                if cells:
                    self.garbage.add(self.rng.choice(cells))

    def configure(self, data):
        if self.running:
            raise ValueError('Pause the game before changing settings')
        tps = data.get('tps', self.tps)
        spawn = data.get('spawn_seconds', self.spawn_seconds)
        cap = data.get('max_garbage', self.max_garbage)
        bots = data.get('random_bots', self.random_bots)
        raw_map = data.get('map', self.map_text)
        if isinstance(tps, bool) or not isinstance(tps, int) or not 1 <= tps <= 20:
            raise ValueError('Ticks per second must be an integer from 1 to 20')
        if isinstance(spawn, bool) or not isinstance(spawn, (int, float)) or not 0.2 <= spawn <= 120:
            raise ValueError('Garbage spawn interval must be 0.2–120 seconds')
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 300:
            raise ValueError('Maximum garbage must be from 1 to 300')
        if isinstance(bots, bool) or not isinstance(bots, int) or not 0 <= bots <= 10:
            raise ValueError('Built-in random bots must be 0–10')
        if not isinstance(raw_map, str):
            raise ValueError('Invalid map')
        map_text, walls, initial = validate_map(raw_map)
        if len(initial) > cap:
            raise ValueError('Map starts with more garbage than the configured maximum')
        if 400 - len(walls) - len(initial) < bots + len(self.connections):
            raise ValueError('Not enough free floor cells for all current players')
        # A settings save deliberately begins a fresh paused round.
        self.map_text, self.walls, self.initial_garbage = map_text, walls, initial
        self.tps, self.spawn_seconds, self.max_garbage, self.random_bots = tps, float(spawn), cap, bots
        self.reset(start=False)


async def broadcast(wss, payload):
    if wss:
        raw = json.dumps(payload, ensure_ascii=False)
        await asyncio.gather(*(ws.send(raw) for ws in list(wss)), return_exceptions=True)


def make_http_response(connection, text, content_type='text/html; charset=utf-8', code=200):
    response = connection.respond(code, text)
    del response.headers['Content-Type']
    response.headers['Content-Type'] = content_type
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "frame-ancestors 'none'"
    return response


def make_http_handler():
    def http_response(connection, request):
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            return None
        path = request.path.split('?', 1)[0]
        if path == '/':
            return make_http_response(connection, (BASE/'index.html').read_text(encoding='utf-8'))
        if path == '/admin':
            return make_http_response(connection, (BASE/'admin.html').read_text(encoding='utf-8'))
        if path == '/health':
            return make_http_response(connection, 'ok\n', 'text/plain; charset=utf-8')
        return make_http_response(connection, 'Not found\n', 'text/plain', 404)
    return http_response


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '8765')))
    parser.add_argument('--map', type=Path, default=BASE/'map.txt')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    game = Competition(args.map)
    admin_password = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_password:
        LOG.warning('ADMIN_PASSWORD not configured: admin access is disabled until set')
    playing: set[Any] = set()

    async def sync():
        state = game.public_state()
        await broadcast(playing | game.viewers, state)
        await broadcast(game.admins, game.admin_state())

    async def handle(ws):
        path = ws.request.path.split('?', 1)[0]
        if path == '/watch':
            game.viewers.add(ws)
            try:
                await ws.send(json.dumps(game.public_state()))
                async for _ in ws:
                    pass
            except ConnectionClosed:
                pass
            finally:
                game.viewers.discard(ws)
            return
        if path == '/admin-ws':
            if not admin_password:
                await ws.close(1008, 'Admin password is not configured')
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                if not isinstance(msg, dict) or msg.get('type') != 'auth' or not isinstance(msg.get('password'), str) or not hmac.compare_digest(msg['password'], admin_password):
                    await ws.send(json.dumps({'type': 'error', 'message': 'Incorrect password'}))
                    await ws.close(1008, 'Unauthorized')
                    return
                game.admins.add(ws)
                await ws.send(json.dumps(game.admin_state()))
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        if not isinstance(msg, dict): raise ValueError('Expected an object')
                        action = msg.get('type')
                        if action == 'start': game.running = True
                        elif action == 'pause':
                            game.running = False
                            for participant in game.participants.values():
                                participant.pending = 'none'
                        elif action == 'reset':
                            for client_ws in list(playing):
                                game.disconnect(client_ws)
                                playing.discard(client_ws)
                                await client_ws.close(1012, 'New competition round')
                            game.reset(start=False)
                        elif action == 'configure':
                            # Validate settings before disconnecting active players.
                            game.configure(msg)
                            for client_ws in list(playing):
                                game.disconnect(client_ws)
                                playing.discard(client_ws)
                                await client_ws.close(1012, 'Settings changed; reconnect')
                        else: raise ValueError('Unknown manager command')
                        await sync()
                    except (ValueError, TypeError) as exc:
                        await ws.send(json.dumps({'type': 'error', 'message': str(exc)}))
            except (ConnectionClosed, asyncio.TimeoutError, json.JSONDecodeError):
                pass
            finally:
                game.admins.discard(ws)
            return
        if path not in ('/', '/play'):
            await ws.close(1008, 'Unknown WebSocket path')
            return
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if not isinstance(msg, dict) or msg.get('type') != 'join':
                raise ValueError('First message must be a join request')
            p = game.join(ws, msg)
            playing.add(ws)
            await ws.send(json.dumps({'type': 'welcome', 'player_id': p.id, 'name': p.name,
                                      'resume_token': p.token if p.kind == 'student' else None,
                                      'width': W, 'height': H, 'tick_ms': round(1000/game.tps)}))
            await sync()
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict) or msg.get('type') != 'move' or msg.get('direction') not in DIRS:
                        raise ValueError('Send {"type":"move","direction":"up|down|left|right|none"}')
                    p = game.participants.get(game.connections.get(ws))
                    if p:
                        p.pending = msg['direction']
                except (ValueError, TypeError) as exc:
                    await ws.send(json.dumps({'type': 'error', 'message': str(exc)}))
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        except (json.JSONDecodeError, ValueError) as exc:
            await ws.send(json.dumps({'type': 'error', 'message': str(exc)}))
            await ws.close(1008, 'Invalid join')
        finally:
            game.disconnect(ws)
            playing.discard(ws)
            await sync()

    async def clock():
        while True:
            await asyncio.sleep(1 / game.tps if game.running else 0.1)
            if game.running:
                game.advance()
                await sync()

    async with serve(handle, args.host, args.port, process_request=make_http_handler(), max_size=8192, ping_interval=20, ping_timeout=20):
        LOG.info('Serving http://%s:%s and WebSockets /play /watch /admin-ws', args.host, args.port)
        await clock()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
