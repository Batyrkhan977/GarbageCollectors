"""Garbage Collectors: classroom competition server.

Python 3.10+, websockets>=14. Teacher login via ADMIN_PASSWORD.
Student WebSockets never receive map/wall coordinates.
"""
import argparse
import asyncio
import hmac
import json
import logging
import math
import os
import random
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

MAX_PLAYERS = 4
# Twenty recognizable colors, chosen for visibility on the dark game board.
PLAYER_COLORS = (
    '#ff6b6b',  # red
    '#ff983f',  # orange
    '#ffd166',  # yellow
    '#80ed99',  # green
    '#31d6bf',  # turquoise
    '#55c7ff',  # sky blue
    '#668cff',  # blue
    '#ac87ff',  # purple
    '#ff80d5',  # pink
    '#f5b971',  # amber
    '#b7df5b',  # lime
    '#33bfa8',  # teal
    '#8dc6ff',  # light blue
    '#d997ff',  # lavender
    '#ff9db0',  # rose
    '#c8e4a5',  # olive
    '#ff8c78',  # coral
    '#7baaff',  # cornflower
    '#e9a8ff',  # lilac
    '#ddd2bc',  # beige
)
DIRS = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0), 'none': (0, 0)}
BASE = Path(__file__).resolve().parent
LOG = logging.getLogger('garbage_collectors')


def validate_map(text):
    if not isinstance(text, str):
        raise ValueError('Map must be text')
    rows = text.strip().splitlines()
    height = len(rows)
    width = len(rows[0]) if rows else 0
    if not (5 <= width <= 20 and 5 <= height <= 20) or any(
        len(row) != width or any(c not in '012' for c in row) for row in rows
    ):
        raise ValueError('Map must be a rectangle of 5–20 rows and 5–20 columns, using only 0, 1, 2')
    walls = {(x, y) for y, row in enumerate(rows) for x, char in enumerate(row) if char == '1'}
    garbage = {(x, y) for y, row in enumerate(rows) for x, char in enumerate(row) if char == '2'}
    return '\n'.join(rows), walls, garbage


def name_ok(value):
    return isinstance(value, str) and 1 <= len(value.strip()) <= 24 and not any(ord(c) < 32 for c in value)


@dataclass
class Participant:
    id: int | None
    name: str
    uid: str
    x: int = -1
    y: int = -1
    score: int = 0
    idle_ticks: int = 0
    pending: str = 'none'
    kind: str = 'student'
    token: str = ''
    connected: bool = False
    team: str | None = None
    color: str = ''
    last_id: int | None = None
    finish_ticks: int | None = None
    trial_status: str = 'waiting'


class Competition:
    def __init__(self, map_path):
        self.rng = random.Random()
        self.map_text, self.walls, self.initial_garbage = validate_map(Path(map_path).read_text(encoding='utf-8'))
        self.width, self.height = self._size(self.map_text)
        self.tps = 5
        self.spawn_seconds = 2.0
        self.max_garbage = 32
        self.random_bots = 2
        self.mode = 'ffa'
        self.duration_seconds = 120
        self.start_positions = self._default_spawns()
        self._validate_positions(self.start_positions, self.walls, self.initial_garbage, self.width, self.height)
        self.garbage = set(self.initial_garbage)
        self.running = False
        self.finished = False
        self.tick = 0
        self.spawn_elapsed = 0.0
        self.spawn_count = 0
        self.participants: dict[str, Participant] = {}
        self.connections: dict[Any, str] = {}
        self.viewers: set[Any] = set()
        self.admins: set[Any] = set()
        self.turn_order: list[str] = []
        self.turn_index = -1
        self.trial_ticks = 0
        self._create_random_bots()
        self._allocate_start_positions()

    def _default_spawns(self):
        w, h = self.width, self.height
        suggested = [(1, 1), (w-2, 1), (1, h-2), (w-2, h-2)]
        floor = [(x,y) for y in range(h) for x in range(w)
                 if (x,y) not in self.walls and (x,y) not in self.initial_garbage]
        found = []
        for p in suggested + floor:
            if p in floor and p not in found:
                found.append(p)
                if len(found) == MAX_PLAYERS:
                    return found
        raise ValueError('Map needs at least four free cells for player spawns')

    @staticmethod
    def _size(map_text):
        lines = map_text.splitlines()
        return len(lines[0]), len(lines)

    @staticmethod
    def _validate_positions(positions, walls, garbage, width, height):
        if not isinstance(positions, list) or len(positions) != MAX_PLAYERS:
            raise ValueError('Provide exactly four initial positions [[x,y], ...]')
        cells = []
        for value in positions:
            if (not isinstance(value, (list, tuple)) or len(value) != 2 or
                any(isinstance(a, bool) or not isinstance(a, int) for a in value)):
                raise ValueError('Starting coordinates must be integer [x,y] pairs')
            x, y = value
            if not 0 <= x < width or not 0 <= y < height:
                raise ValueError(f'Starting coordinates must be within 0–{width-1} and 0–{height-1}')
            if (x,y) in walls or (x,y) in garbage:
                raise ValueError(f'Initial position ({x},{y}) must be empty floor')
            cells.append((x,y))
        if len(set(cells)) != MAX_PLAYERS:
            raise ValueError('All four initial player positions must be different')

    def _active(self):
        return sorted((p for p in self.participants.values() if p.connected), key=lambda p: p.id)

    def _unused_id(self):
        used = {p.id for p in self.participants.values() if p.connected}
        for number in range(1, MAX_PLAYERS+1):
            if number not in used:
                return number
        raise ValueError('Game is full: only four players can connect')

    def _choose_color(self):
        """Random distinct colors for simultaneously active entries where possible."""
        taken = {p.color for p in self.participants.values() if p.connected and p.color}
        available = [c for c in PLAYER_COLORS if c not in taken]
        return self.rng.choice(available or PLAYER_COLORS)

    def _create_random_bots(self):
        for n in range(self.random_bots):
            bot = Participant(None, f'Random Bot {n+1}', uuid.uuid4().hex, kind='random', connected=True)
            bot.id = self._unused_id()
            bot.last_id = bot.id
            bot.color = self._choose_color()
            self.participants[bot.uid] = bot

    def _allocate_start_positions(self):
        for p in self._active():
            p.last_id = p.id
            p.x, p.y = self.start_positions[p.id-1]

    def _sanitize_ids(self):
        """Defensively fix duplicates, ensuring IDs 1..4 remain unique."""
        seen = set()
        for p in self._active():
            if p.id not in range(1,5) or p.id in seen:
                p.id = next(i for i in range(1,5) if i not in seen)
            seen.add(p.id)
            p.last_id = p.id

    def join(self, ws, msg):
        if self.running or self.finished or self.tick > 0 or self.turn_order:
            raise ValueError('Joining is only allowed before a round starts; ask the manager to reset')
        role = msg.get('role', 'bot')
        if role not in ('bot', 'human'):
            raise ValueError('role must be bot or human')
        name = msg.get('name')
        if not name_ok(name):
            raise ValueError('Name must be 1–24 characters without control characters')
        name = name.strip()
        token = msg.get('resume_token')
        if role == 'bot' and isinstance(token, str) and token:
            for p in self.participants.values():
                if p.kind == 'student' and hmac.compare_digest(p.token, token):
                    if p.connected or p.name != name:
                        raise ValueError('Token belongs to an active or differently named bot')
                    p.id = self._unused_id()
                    p.last_id = p.id
                    p.connected = True
                    p.pending = 'none'
                    p.x, p.y = self.start_positions[p.id-1]
                    self.connections[ws] = p.uid
                    return p
            raise ValueError('Invalid resume_token; start a new round or remove your stale token')
        if any(p.name.casefold() == name.casefold() for p in self.participants.values()):
            raise ValueError('Name already used in this round; reconnect using your resume_token')
        pid = self._unused_id()
        p = Participant(pid, name, uuid.uuid4().hex, kind='student' if role == 'bot' else 'human',
                        token=secrets.token_urlsafe(32) if role == 'bot' else '', connected=True)
        p.x, p.y = self.start_positions[pid-1]
        p.last_id = pid
        p.color = self._choose_color()
        self.participants[p.uid] = p
        self.connections[ws] = p.uid
        return p

    def disconnect(self, ws):
        uid = self.connections.pop(ws, None)
        p = self.participants.get(uid)
        if p:
            p.connected = False
            p.pending = 'none'
            p.x = p.y = -1
            p.last_id = p.id
            p.id = None  # Released immediately; next participant receives least unused ID.
            if self.mode == 'time_attack' and self.running and uid == self.current_uid:
                p.trial_status = 'disconnected'
                self._next_trial()
            if p.kind == 'human' and not self.running:
                self.participants.pop(uid, None)

    @property
    def current_uid(self):
        if 0 <= self.turn_index < len(self.turn_order):
            return self.turn_order[self.turn_index]
        return None

    def reset(self):
        self.running = False
        self.finished = False
        self.tick = 0
        self.spawn_elapsed = 0.0
        self.spawn_count = 0
        self.garbage = set(self.initial_garbage)
        self.participants.clear()
        self.connections.clear()
        self.turn_order = []
        self.turn_index = -1
        self.trial_ticks = 0
        self._create_random_bots()
        self._allocate_start_positions()

    def start(self):
        if self.finished:
            raise ValueError('Round finished; reset to play again')
        if not self.running and self.tick == 0 and not self.turn_order:
            roster = self._active()
            if self.mode == '2v2' and len(roster) != 4:
                raise ValueError('2v2 requires exactly four connected players')
            if self.mode == 'time_attack':
                if not self.initial_garbage:
                    raise ValueError('Time Attack requires at least one starting garbage piece')
                self.turn_order = [p.uid for p in roster]
                if not self.turn_order:
                    raise ValueError('Connect at least one player first')
                self.rng.shuffle(self.turn_order)
                self.turn_index = 0
                self._begin_trial()
            elif self.mode == '2v2':
                # Stable and unambiguous: slots 1+3 red, 2+4 blue.
                for p in roster:
                    p.team = 'red' if p.id % 2 else 'blue'
                    p.color = '#ff6b6b' if p.team == 'red' else '#668cff'
        self.running = True

    def _begin_trial(self):
        self.garbage = set(self.initial_garbage)
        self.trial_ticks = 0
        self.spawn_elapsed = 0
        self.spawn_count = 0
        for p in self.participants.values():
            p.x = p.y = -1
            p.pending = 'none'
        p = self.participants[self.current_uid]
        if p.connected:
            p.x, p.y = self.start_positions[p.id-1]
            p.trial_status = 'playing'
        else:
            p.trial_status = 'disconnected'
            self._next_trial()

    def _next_trial(self):
        self.turn_index += 1
        if self.turn_index >= len(self.turn_order):
            self.finished = True
            self.running = False
            for p in self.participants.values():
                p.x = p.y = -1
            return
        self._begin_trial()

    def advance(self):
        if not self.running:
            return
        self.tick += 1
        self._sanitize_ids()
        if self.mode == 'time_attack':
            uid = self.current_uid
            if not uid:
                self.finished, self.running = True, False
                return
            p = self.participants[uid]
            if not p.connected:
                p.trial_status = 'disconnected'
                self._next_trial()
                return
            self.trial_ticks += 1
            movers = [p]
        else:
            movers = self._active()
            if movers:
                offset = self.tick % len(movers)
                movers = movers[offset:] + movers[:offset]
        for p in movers:
            direction = self.rng.choice(['up','down','left','right']) if p.kind == 'random' else p.pending
            p.pending = 'none'
            if direction == 'none':
                p.idle_ticks += 1
                continue
            dx,dy = DIRS[direction]
            nx,ny = p.x+dx,p.y+dy
            dest = (nx,ny)
            if not 0<=nx<self.width or not 0<=ny<self.height or dest in self.walls:
                continue
            p.x,p.y = dest
            if dest in self.garbage:
                self.garbage.remove(dest)
                p.score += 1
        if self.mode == 'time_attack':
            if not self.garbage:
                p = self.participants[self.current_uid]
                p.finish_ticks = self.trial_ticks
                p.trial_status = 'finished'
                self._next_trial()
            return
        # An integer tick deadline is the single source of truth. Crossing one
        # spawn interval schedules exactly ONE new garbage piece (never two).
        # ceil avoids spawning early when the interval isn't a whole tick.
        if self.tick >= math.ceil((self.spawn_count + 1) * self.spawn_seconds * self.tps):
            self.spawn_count += 1
            if len(self.garbage) < self.max_garbage:
                occupied = {(p.x, p.y) for p in self._active()}
                candidates = [(x, y) for y in range(self.height) for x in range(self.width)
                              if (x, y) not in self.walls and (x, y) not in self.garbage
                              and (x, y) not in occupied]
                if candidates:
                    self.garbage.add(self.rng.choice(candidates))
        if self.tick >= self.duration_seconds * self.tps:
            self.running = False
            self.finished = True

    def public_state(self):
        roster = self._active()
        if self.mode == 'time_attack':
            ranking = sorted(self.participants.values(), key=lambda p: (p.finish_ticks is None,
                       p.finish_ticks if p.finish_ticks is not None else float('inf'),-p.score,p.name.casefold()))
        else:
            ranking = sorted(self.participants.values(),key=lambda p:(-p.score,p.idle_ticks,p.name.casefold()))
        teams = {team:sum(p.score for p in self.participants.values() if p.team==team)
                 for team in ('red','blue')}
        return {'type':'state','tick':self.tick,'tick_ms':round(1000/self.tps),
                'running':self.running,'finished':self.finished,'mode':self.mode,
                'duration_seconds':self.duration_seconds,
                'remaining_seconds':max(0, round(self.duration_seconds-self.tick/self.tps, 2)) if self.mode!='time_attack' else None,
                'width':self.width,'height':self.height,'teams':teams,
                'turn_order':[{'name':self.participants[uid].name,'uid':uid} for uid in self.turn_order],
                'active_turn':self.current_uid if self.mode=='time_attack' and not self.finished else None,
                'trial_ticks':self.trial_ticks,
                'players':[{'id':p.id,'uid':p.uid,'name':p.name,'x':p.x,'y':p.y,
                            'score':p.score,'idle_ticks':p.idle_ticks,'kind':p.kind,
                            'is_bot':p.kind!='human','connected':p.connected,'team':p.team,'color':p.color}
                           for p in roster],
                'leaderboard':[{'id':p.id,'uid':p.uid,'name':p.name,'score':p.score,
                                'idle_ticks':p.idle_ticks,'kind':p.kind,'connected':p.connected,
                                'team':p.team,'color':p.color,'last_id':p.last_id,'finish_ticks':p.finish_ticks,'trial_status':p.trial_status}
                               for p in ranking],
                'garbage':[list(c) for c in sorted(self.garbage)],'remaining':len(self.garbage)}

    def admin_state(self):
        return {**self.public_state(),'type':'admin_state','map':self.map_text,
                'start_positions':[list(x) for x in self.start_positions],
                'tps':self.tps,'spawn_seconds':self.spawn_seconds,
                'max_garbage':self.max_garbage,'random_bots':self.random_bots}

    def configure(self, data):
        if self.running:
            raise ValueError('Pause the game before changing settings')
        tps = data.get('tps',self.tps)
        cap = data.get('max_garbage',self.max_garbage)
        spawn = data.get('spawn_seconds',self.spawn_seconds)
        bots = data.get('random_bots',self.random_bots)
        duration = data.get('duration_seconds',self.duration_seconds)
        mode = data.get('mode',self.mode)
        raw_map = data.get('map',self.map_text)
        positions = data.get('start_positions',[list(pos) for pos in self.start_positions])
        if type(tps) is not int or not 1<=tps<=50:
            raise ValueError('Ticks per second must be from 1 to 50')
        if type(cap) is not int or not 1<=cap<=50:
            raise ValueError('Maximum garbage must be from 1 to 50')
        if type(bots) is not int or not 0<=bots<=4:
            raise ValueError('Built-in random bots must be from 0 to 4')
        if isinstance(spawn,bool) or not isinstance(spawn,(int,float)) or not 0.2<=spawn<=120:
            raise ValueError('Garbage spawn interval must be 0.2–120 seconds')
        if type(duration) is not int or not 1<=duration<=86400:
            raise ValueError('Round duration must be from 1 to 86400 seconds')
        if mode not in ('ffa','2v2','time_attack'):
            raise ValueError('Mode must be ffa, 2v2 or time_attack')
        if not isinstance(raw_map,str):
            raise ValueError('Invalid map text')
        map_text,walls,garbage = validate_map(raw_map)
        self._validate_positions(positions,walls,garbage,*self._size(map_text))
        if len(garbage)>cap:
            raise ValueError('Map has more starting garbage than the maximum allowed')
        if mode=='time_attack' and not garbage:
            raise ValueError('Time Attack needs at least one starting garbage')
        if mode=='2v2' and bots>4:
            raise ValueError('2v2 allows four total players')
        self.map_text,self.walls,self.initial_garbage=map_text,walls,garbage
        self.width,self.height=self._size(map_text)
        self.start_positions=[tuple(pos) for pos in positions]
        self.tps,self.max_garbage,self.spawn_seconds=tps,cap,float(spawn)
        self.random_bots,self.duration_seconds,self.mode=bots,duration,mode
        self.reset()


async def broadcast(wss, payload):
    if wss:
        raw = json.dumps(payload,ensure_ascii=False)
        await asyncio.gather(*(ws.send(raw) for ws in list(wss)),return_exceptions=True)


def http_response(connection,text,content_type='text/html; charset=utf-8',code=200):
    response=connection.respond(code,text)
    del response.headers['Content-Type']
    response.headers['Content-Type']=content_type
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="frame-ancestors 'none'"
    return response


def make_http_handler():
    def handler(connection,request):
        if request.headers.get('Upgrade','').lower()=='websocket':
            return None
        path=request.path.split('?',1)[0]
        if path=='/': return http_response(connection,(BASE/'index.html').read_text(encoding='utf-8'))
        if path=='/admin': return http_response(connection,(BASE/'admin.html').read_text(encoding='utf-8'))
        if path=='/health': return http_response(connection,'ok\n','text/plain; charset=utf-8')
        return http_response(connection,'Not found\n','text/plain',404)
    return handler


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=int(os.getenv('PORT','8765')))
    parser.add_argument('--map',type=Path,default=BASE/'map.txt')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    game=Competition(args.map)
    password=os.environ.get('ADMIN_PASSWORD','')
    if not password: LOG.warning('ADMIN_PASSWORD not set: admin login disabled')
    playing=set()

    async def sync():
        await broadcast(playing | game.viewers,game.public_state())
        await broadcast(game.admins,game.admin_state())

    async def close_players():
        for client in list(playing):
            game.disconnect(client)
            playing.discard(client)
            await client.close(1012,'New game settings or reset: reconnect')

    async def handle(ws):
        path=ws.request.path.split('?',1)[0]
        if path=='/watch':
            game.viewers.add(ws)
            try:
                await ws.send(json.dumps(game.public_state()))
                async for _ in ws: pass
            except ConnectionClosed: pass
            finally: game.viewers.discard(ws)
            return
        if path=='/admin-ws':
            if not password:
                await ws.close(1008,'ADMIN_PASSWORD is not configured')
                return
            try:
                raw=await asyncio.wait_for(ws.recv(),timeout=10)
                msg=json.loads(raw)
                if not isinstance(msg,dict) or msg.get('type')!='auth' or not isinstance(msg.get('password'),str) or not hmac.compare_digest(msg['password'],password):
                    await ws.send(json.dumps({'type':'error','message':'Incorrect password'}))
                    await ws.close(1008,'Unauthorized')
                    return
                game.admins.add(ws)
                await ws.send(json.dumps(game.admin_state()))
                async for raw in ws:
                    try:
                        msg=json.loads(raw)
                        if not isinstance(msg,dict): raise ValueError('Expected JSON object')
                        action=msg.get('type')
                        if action=='start': game.start()
                        elif action=='pause':
                            game.running=False
                            for p in game.participants.values():p.pending='none'
                        elif action=='reset':
                            await close_players()
                            game.reset()
                        elif action=='configure':
                            # Validate without mutating game or disconnecting students first.
                            # A temporary clone checks ranges, map and coordinates.
                            test=Competition(args.map)
                            test.configure(msg)
                            await close_players()
                            game.configure(msg)
                        elif action=='skip_trial':
                            if game.mode!='time_attack' or not game.turn_order or game.finished:
                                raise ValueError('No active Time Attack trial')
                            current=game.participants[game.current_uid]
                            current.trial_status='skipped'
                            game._next_trial()
                        else: raise ValueError('Unknown manager command')
                        await sync()
                    except (ValueError,TypeError) as exc:
                        await ws.send(json.dumps({'type':'error','message':str(exc)}))
            except (ConnectionClosed,asyncio.TimeoutError,json.JSONDecodeError): pass
            finally: game.admins.discard(ws)
            return
        if path not in ('/','/play'):
            await ws.close(1008,'Unknown WebSocket path')
            return
        try:
            raw=await asyncio.wait_for(ws.recv(),timeout=10)
            msg=json.loads(raw)
            if not isinstance(msg,dict) or msg.get('type')!='join':
                raise ValueError('First message must be join request')
            p=game.join(ws,msg)
            playing.add(ws)
            await ws.send(json.dumps({'type':'welcome','player_id':p.id,'uid':p.uid,
                        'name':p.name,'resume_token':p.token if p.kind=='student' else None,
                        'width':self.width,'height':self.height,'tick_ms':round(1000/game.tps)}))
            await sync()
            async for raw in ws:
                try:
                    msg=json.loads(raw)
                    if not isinstance(msg,dict) or msg.get('type')!='move' or msg.get('direction') not in DIRS:
                        raise ValueError('Expected move direction: up, down, left, right or none')
                    player=game.participants.get(game.connections.get(ws))
                    if player: player.pending=msg['direction']
                except (TypeError,ValueError) as exc:
                    await ws.send(json.dumps({'type':'error','message':str(exc)}))
        except (ConnectionClosed,asyncio.TimeoutError): pass
        except (json.JSONDecodeError,ValueError) as exc:
            await ws.send(json.dumps({'type':'error','message':str(exc)}))
            await ws.close(1008,'Invalid join')
        finally:
            game.disconnect(ws)
            playing.discard(ws)
            await sync()

    async def clock():
        loop=asyncio.get_running_loop()
        next_tick=None
        while True:
            if not game.running:
                next_tick=None
                await asyncio.sleep(.05)
                continue
            interval=1/game.tps
            if next_tick is None: next_tick=loop.time()+interval
            await asyncio.sleep(max(0,next_tick-loop.time()))
            game.advance()
            await sync()
            next_tick+=interval
            if next_tick<loop.time(): next_tick=loop.time()+interval

    async with serve(handle,args.host,args.port,process_request=make_http_handler(),max_size=32768,ping_interval=20,ping_timeout=20):
        LOG.info('Serving http://%s:%d; WebSockets: /play /watch /admin-ws',args.host,args.port)
        await clock()


if __name__=='__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
