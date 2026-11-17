"""Example competition bot. Install: pip install websockets>=14,<17.
Run: python student_bot.py wss://YOUR-SERVICE.onrender.com/play MyBot

Each student should modify choose_move(state, my_id). The server keeps walls
secret, so smarter bots can infer them from their attempted moves and positions.
"""
import asyncio
import json
import random
import sys
from pathlib import Path
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def choose_move(state, my_id):
    """Replace with your bot's strategy; return up/down/left/right/none."""
    return random.choice(['up', 'down', 'left', 'right'])


async def run(url, name):
    # This file stores an unguessable reconnect token: don't commit it to GitHub.
    token_path = Path('bot_token_' + ''.join(c for c in name if c.isalnum()) + '.txt')
    while True:
        try:
            async with connect(url) as ws:
                join = {'type': 'join', 'role': 'bot', 'name': name}
                if token_path.exists():
                    join['resume_token'] = token_path.read_text().strip()
                await ws.send(json.dumps(join))
                my_id = None
                last_tick = -1
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get('type') == 'welcome':
                        my_id = msg['player_id']
                        if msg.get('resume_token'):
                            token_path.write_text(msg['resume_token'])
                        print('Joined as', name, 'id', my_id)
                    elif msg.get('type') == 'state' and my_id is not None:
                        if not msg['running'] or msg['tick'] <= last_tick:
                            continue
                        last_tick = msg['tick']
                        move = choose_move(msg, my_id)
                        if move not in ('up','down','left','right','none'):
                            move = 'none'
                        await ws.send(json.dumps({'type':'move','direction':move}))
                        mine = next((p for p in msg['players'] if p['id'] == my_id), None)
                        if mine:
                            print(f"tick={last_tick} position=({mine['x']},{mine['y']}) garbage={mine['score']} idle={mine['idle_ticks']}")
                    elif msg.get('type') == 'error':
                        print('Server error:', msg.get('message'))
                        # Stale token likely means teacher started a new round.
                        if 'resume_token' in msg.get('message', '') and token_path.exists():
                            token_path.unlink()
                        break
        except (OSError, ConnectionError, ConnectionClosed) as e:
            print('Connection interrupted:', e)
        print('Reconnecting in 3 seconds...')
        await asyncio.sleep(3)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python student_bot.py wss://YOUR-SERVICE.onrender.com/play "Your Bot Name"')
        raise SystemExit(2)
    try:
        asyncio.run(run(sys.argv[1], sys.argv[2]))
    except KeyboardInterrupt:
        print('Bot stopped')
