from __future__ import annotations
import traceback
import marble_levels as levels
import marble_physics as mp
import marble_bridge as mb
import marble_text as mt
import marble_sound as ms
from marble_tables import DATA_TABLES

r=levels.Roms()
print('Native table backend:', type(r.cpu).__name__)
print('Playfield tiles',r.tiles().shape,'sprites',r.mo_tiles().shape,'text chars',r.text_chars().shape)
print('Sound command size:',len(ms.SoundTables(r).type))
for race in range(6):
 try:
  print('Race',race+1,flush=True)
  w=levels.decode_level(r,race)
  g,_=levels.build_heights(r,race)
  f=mp.HeightField(g,levels.HEIGHT_Y_OFFSET[race])
  marble=mb.EngineMarble(f,r.cpu,race,g,players=2 if race==3 else 1)
  for frame in range(800):
   move=((-1,0),(0,1),(1,0),(0,-1))[frame//110%4]
   marble.step(*move)
  print('  OK',w.shape,g.shape,'mode',marble.mode,'frame',marble.frame)
 except Exception as ex:
  traceback.print_exc(limit=8)
  raise
print('Text screens:')
t=mt.ArcadeHud(r.cpu,r.text_chars()); hs=mt.HighScores(r.cpu)
t.attract(0,hs);t.credits(3);t.credits_page();t.demo_start(0)
for race in range(6):
 for n in (1,2):
  t.players=n
  t.race_start(race,[60]*n,20,[0]*n,active=[True]*n)
  for _ in range(100):t.intro_step()
for i in (0,1):
 t.t.end_screen(i,2,12,3)
 t.t.ready(2,i);t.t.time_up(2,[i]);t.t.bonus(i,1000)
print('HUD OK',len(hs.entries))
for first,target in [(110,20),(117,35),(130,45),(210,70)]:
 a=levels.decode_alt_rows(r,5,first,target,3)
 print('Ultimate alternate rows',first,target,a.shape)
print('ALL PASSED')
