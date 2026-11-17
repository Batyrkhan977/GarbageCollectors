"""Run with: python -m unittest -v test_competition"""
import unittest
from pathlib import Path
from server import Competition

ROOT=Path(__file__).resolve().parent


class CompetitionTests(unittest.TestCase):
    def new(self):
        g=Competition(ROOT/'map.txt')
        g.configure({'random_bots':0})
        return g

    def test_settings_bounds_and_positions(self):
        g=self.new()
        g.configure({'tps':50,'max_garbage':50,'random_bots':4})
        self.assertEqual(len(g._active()),4)
        self.assertEqual([p.id for p in g._active()],[1,2,3,4])
        for bad in [0,51]:
            with self.assertRaises(ValueError): g.configure({'tps':bad})
            with self.assertRaises(ValueError): g.configure({'max_garbage':bad})
        with self.assertRaises(ValueError): g.configure({'start_positions':[[1,1]]*4})
        with self.assertRaises(ValueError): g.configure({'random_bots':5})

    def test_id_reuse_max4_and_resume(self):
        g=self.new()
        sockets=[object() for _ in range(4)]
        players=[g.join(s,{'name':f'S{i}','role':'bot'}) for i,s in enumerate(sockets)]
        self.assertEqual([p.id for p in players],[1,2,3,4])
        with self.assertRaisesRegex(ValueError,'full'):
            g.join(object(),{'name':'extra','role':'bot'})
        token=players[1].token
        g.disconnect(sockets[1])
        fresh=g.join(object(),{'name':'Fresh','role':'bot'})
        self.assertEqual(fresh.id,2)
        self.assertEqual(len(set(p.id for p in g._active())),4)
        g.disconnect(next(ws for ws,uid in g.connections.items() if uid==fresh.uid))
        resumed=g.join(object(),{'name':'S1','role':'bot','resume_token':token})
        self.assertEqual(resumed.id,2)
        self.assertNotIn('map',str(g.public_state()))
        self.assertNotIn('walls',str(g.public_state()))

    def test_ffa_ends_on_duration(self):
        g=self.new()
        g.configure({'mode':'ffa','tps':5,'duration_seconds':2})
        p=g.join(object(),{'name':'A','role':'bot'})
        g.start()
        for _ in range(10):
            p.pending='none';g.advance()
        self.assertEqual(p.idle_ticks,10)
        self.assertTrue(g.finished)
        self.assertFalse(g.running)

    def test_blocked_move_is_not_idle(self):
        g=self.new()
        ws=object(); p=g.join(ws,{'name':'A','role':'bot'})
        p.x,p.y=0,0
        g.start()
        p.pending='left';g.advance()
        self.assertEqual(p.idle_ticks,0)
        self.assertEqual((p.x,p.y),(0,0))

    def test_2v2_roster_teams(self):
        g=self.new()
        g.configure({'mode':'2v2'})
        with self.assertRaisesRegex(ValueError,'exactly four'):
            g.start()
        for i in range(4): g.join(object(),{'role':'bot','name':str(i)})
        g.start()
        self.assertEqual([p.team for p in g._active()],['red','blue','red','blue'])
        self.assertEqual(g.public_state()['teams'],{'red':0,'blue':0})

    def test_time_attack_sequential_and_no_spawn(self):
        g=self.new()
        g.configure({'mode':'time_attack','tps':50,'max_garbage':50,'spawn_seconds':0.2})
        for n in range(2):g.join(object(),{'role':'bot','name':str(n)})
        g.start()
        self.assertEqual(len(g.turn_order),2)
        self.assertEqual(len([p for p in g._active() if p.x>=0]),1)
        initial=set(g.initial_garbage)
        for _ in range(100):
            for p in g._active():p.pending='none'
            g.advance()
        self.assertTrue(g.running)
        self.assertTrue(g.garbage.issubset(initial))
        current=g.participants[g.current_uid]
        g.garbage.clear();g.advance()
        self.assertEqual(current.finish_ticks,101)
        self.assertEqual(current.trial_status,'finished')
        self.assertEqual(g.garbage,initial)
        self.assertEqual(len([p for p in g._active() if p.x>=0]),1)
        other=g.participants[g.current_uid]
        g.garbage.clear();g.advance()
        self.assertTrue(g.finished)
        self.assertFalse(g.running)
        self.assertEqual(other.trial_status,'finished')


if __name__=='__main__':unittest.main()
