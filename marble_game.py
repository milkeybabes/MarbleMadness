#!/usr/bin/env python3
"""Marble Madness prototype in pygame, using level graphics and height maps
predecoded as native Python game data (no ROMs required).

    pip install pygame-ce numpy pillow
    python3 marble_game.py [race 1-6]

(pygame-ce, the Community Edition, is imported as `pygame`, so no code changes
are needed.  If the original pygame is installed too, run
`pip uninstall pygame` first: both packages provide the same module name.)

Controls
    Arrows / WASD    roll (screen directions)
    M                toggle trackball mode (mouse/trackball captured while on)
    [ ]              trackball sensitivity down / up
    Click            put the marble where you clicked (testing aid)
    1-6              switch race     R  restart race   (Shift+1-6, Shift+R)
    H                toggle height-map overlay
    Esc              quit

Debug positioning
    F1               debug mode on/off: the game freezes and the marble is moved
                     by hand, no physics (no friction, rolling or falling)
    Arrows           move it (left/right 2 px, up/down 1 px a frame; Shift x4)
    Ctrl+Arrows      one arcade unit per press along one axis
                     (Right/Left = y +1/-1, Down/Up = x +1/-1)
    PgUp / PgDn      lift it above the floor / lower it (Home: back on the floor)
    Click            put it on the floor cell you clicked
    P                log the spot to debug_spots.txt (and the console):
                     position, screen position, visibility and wall masks

    F2               jump to the next race (Ultimate wraps round to Practice)
    F3               wall-mask overlay on/off: the 8-px screen columns the
                     arcade checked for walls in front of the marble (green = clear,
                     yellow = masked by the wall-height calculation, orange = masked by
                     the ROM's hand-placed table, red = fully covered), the mask shapes
                     tinted the same colour, and the marble's 16x16 box (cyan; dotted
                     when the arcade hides the marble completely)

    F6               start / stop recording a raw AVI of the game picture (no text)
                     into recordings/ - 59.92 frames a second like MAME, for
                     side-by-side comparison; big files are split at 1.9 GB

    F7               sound on / off ( the recordings in sounds/ next to
                     this file, see marble_sound.py)
    --soundlog       write every sound command to sound_log.txt (frame, race,
                     who sent it, the command, player 1's zone / mode / surface)
    --nosound        start with the sound off
    --attract-music  the operator option (0x4003DC bit 0x4000) that plays a tune on
                     the high-score page after the credits page

The arcade flow (python3 marble_game.py, no race number)
    5                insert a coin (1 coin per play)
    1 / 2            start a one- or two-player game (two players use two credits)
    Player 1         arrow keys (or the mouse/trackball with M)
    Player 2         R up, F down, D left, G right
                     A marble that drops off the bottom of the screen in a two-player
                     race is put back near the other one: 5 seconds off its clock and
                     a death (the arcade's FUN_251DE).  Each player has their own clock;
                     run out and you're out; the other carries on.
    Giving a race number (e.g. 3) skips the attract page.
    --demo           explicitly start the automatic arcade demonstration/attract loop

Replaying a MAME capture (mm_capture.lua's JSON)
    --replay=capture.json   play back MAME's own trackball input and tick timing
                            from the race start, frame for frame
    --avi=out.avi           record from the first frame (stops at the end)

    --free                  no time limit (the clock shows FREE; F4 toggles it)

    F4               stop / restart the race clock (F1 stops it too, until F4)
    F5               pause, with every wall mask the arcade can use on this screen drawn
                     half transparent (white: from the wall heights, orange: the ROM's
                     hand-placed masks); F5 again to carry on

    F12              save a screenshot to screenshots/ (works any time; with
                     the marble's details go to debug_spots.txt too)

    python3 marble_game.py 1 --trackball    start with trackball mode on
    python3 marble_game.py --demo          automatic arcade demonstration/attract loop

Race rules follow the arcade: Practice and Beginner give 60 s "time to finish";
Intermediate +35 s, Aerial +30 s, Silly +20 s and Ultimate +20 s are added to
whatever time is left.  Run out of time and the game is over.

The translated arcade engine is always enabled. The legacy stand-in physics
module is retained in the source tree for reference, not selected at runtime.
"""
import math
import os
import sys
import time

import numpy as np

import marble_levels as ml
import marble_physics as mp
import marble_bridge as mb
import marble_text as mt
from marble_avi import RawAvi
import marble_sound as ms

W, H, SCALE = 288, 240, 3          # the game picture (level is 288 px wide)
BORDER = 24                         # the arcade's screen is 336 px: 3 columns either side
FULL_W = W + 2 * BORDER             # (black in a race; the text and balls use them)

# Time per race, from the arcade (see marble-love level-intro-banner-resume.ts).
RACE_TIME = [60, 60, 35, 30, 20, 20]
FRESH_TIME = {0, 1}                 # these races reset the clock; the rest add to it
GOAL_MARGIN = 72                    # px from the far end of the course that counts as finished


def load_race(roms, tiles, lv):
    words = ml.decode_level(roms, lv)
    idx = ml.render_index(words, tiles)
    img = roms.palette(ml.LEVEL_PALETTE[lv])[idx]
    grid, _ = ml.build_heights(roms, lv)
    field = mp.HeightField(grid, ml.HEIGHT_Y_OFFSET[lv])
    relief = np.array(ml.render_relief(grid, (img.shape[1], img.shape[0]), ml.HEIGHT_Y_OFFSET[lv]))
    return img, field, relief, idx


def main():
    import pygame

    roms = ml.Roms()
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) > 1 or (args and args[0] not in '123456'):
        sys.exit('Usage: python marble_game.py [race 1-6] [--demo] [--free] [--trackball]')
    race = (int(args[0]) - 1) if args else 0
    mouse_ball = '--trackball' in sys.argv
    use_engine = True  # Always run the translated arcade engine; no placeholder mode
    opt = lambda name: next((a.split('=', 1)[1] for a in sys.argv if a.startswith(name + '=')), None)
    replay_path = opt('--replay')                 # a MAME capture (mm_capture.lua) to play back
    avi_path = opt('--avi')                       # start recording at once (F6 toggles)
    if '--demo' in sys.argv and args:
        sys.exit('--demo cannot be combined with a race number')
    sensitivity = 0.15
    tiles = roms.tiles()
    global SPRITE_ART

    pygame.mixer.pre_init(48000, -16, 2, 1024)    # the recordings' format
    pygame.init()
    snd = None
    if use_engine:
        snd = ms.Sounds(roms, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds'))
        if '--nosound' in sys.argv:
            snd.volume = 0.0
    attract_music = '--attract-music' in sys.argv
    sound_log = open('sound_log.txt', 'w') if '--soundlog' in sys.argv else None
    frame_no = [0]

    def log_sound(n, who):
        if sound_log:
            z = ''
            if hasattr(marble, 'm'):
                a = mb.me.PLAYERS[0]
                z = (f'zone {marble.m.rb(a + 0x1b):3d} mode {mb.me.s8(marble.m.rb(a + 0x1a)):3d} '
                     f'surface {marble.m.rb(a + 0x58):02X}')
            sound_log.write(f'{frame_no[0]:7d}  race {race + 1}  {who:6s} {n:02X}   {z}\n')
            sound_log.flush()

    def sound(n):
        """A sound command sent by the game flow itself (FUN_158AC from the main loop)."""
        if snd is not None:
            log_sound(n, 'game')
            snd.send(n)
    SPRITE_ART = make_sprite_art(pygame, roms.mo_tiles())
    screen = pygame.display.set_mode((FULL_W * SCALE, H * SCALE))
    full = pygame.Surface((FULL_W, H))                    # the arcade's whole screen
    view = full.subsurface((BORDER, 0, W, H))            # the game picture in the middle
    font = pygame.font.SysFont(None, 16)
    clock = pygame.time.Clock()

    cache = {}
    level_pal = None
    level_idx = None
    ult_cycle = {'on': False, 'count': 0, 'step': 0}   # 0x400460 / 0x40045E (FUN_26D4E)

    def apply_palette(n):
        """FUN_26B88: palette change n into palette RAM - playfield colours (0x200-0x27F)
        recolour the level picture, motion-object colours (0x100-0x17F) the sprites."""
        pf = False
        for k, w in roms.palette_script(n).items():
            if 0x200 <= k < 0x280 and level_pal is not None:
                level_pal[k - 0x200] = ml.Roms.rgb(w); pf = True
            elif 0x100 <= k < 0x180 and (k & 15) < 8 and SPRITE_ART:
                SPRITE_ART['palette'][(k - 0x100) >> 4, k & 15] = ml.Roms.rgb(w)
                SPRITE_ART['objects'] = {}
        if pf and level_idx is not None:
            pygame.surfarray.blit_array(level, level_pal[level_idx].swapaxes(0, 1))
    logo_pal = roms.palette(0, script=0x13)   # the page's colours: race 0's (26B2A(0)), then change 0x13
    rng_state = [b'\0\0']               # 0x4003A6 between races (the arcade never resets it)
    nplay = 1                            # players in this game
    credits = 0
    arcade = not replay_path and not args   # the coin-op flow: attract, coin, start

    def start(lv):
        if lv not in cache:
            pygame.display.set_caption(f'Loading {ml.RACES[lv]} race...')
            img, field, relief, idx = load_race(roms, tiles, lv)
            surf = pygame.surfarray.make_surface(img.swapaxes(0, 1))
            rel = pygame.surfarray.make_surface(relief.swapaxes(0, 1))
            rel.set_alpha(150)
            cache[lv] = (surf, field, rel, img.shape[0], idx)
        pygame.display.set_caption(f'Marble Madness prototype: {ml.RACES[lv]} race')
        surf, field, rel, height, idx = cache[lv]
        surf = surf.copy()                       # animated scenery is drawn into this copy
        nonlocal level_pal, level_idx
        level_idx = idx.copy()                   # (and as palette numbers, for colour changes)
        level_pal = roms.palette(ml.LEVEL_PALETTE[lv])
        set_sprite_colours(roms, lv)
        marble = mb.EngineMarble(field, roms.cpu, lv, field.g, players=nplay) if use_engine else mp.Marble(field)
        if use_engine:                           # the random numbers run on from race to race
            marble.m.ram[0x3a6:0x3a8] = rng_state[0]
        return surf, field, rel, height, marble

    def goal_reached(lv, marble, level_h):
        if hasattr(marble, 'finished'):              # engine: the arcade's own goal line (FUN_1365C)
            return marble.finished
        if marble.airborne or marble.broken:
            return False
        y = marble.screen_pos()[1]
        return y < GOAL_MARGIN if lv == 4 else y > level_h - GOAL_MARGIN   # Silly runs uphill

    level, field, relief, level_h, marble = start(race)
    replaying = False
    if replay_path:                                # follow MAME's own inputs and ticks
        pygame.display.set_caption('Loading the replay...')
        cap_race = marble.load_replay(replay_path)
        if cap_race != race:
            race = cap_race
            level, field, relief, level_h, marble = start(race)
            marble.load_replay(replay_path)
        replaying = True
    avi = None
    if avi_path:
        avi = RawAvi(avi_path, W, H)
    time_left = float(RACE_TIME[race])
    state, banner = 'play', 120        # attract | intro | play | goal | over | won
    hud = None                           # the arcade's text layer (engine mode)
    if use_engine and not replaying:
        hud = mt.ArcadeHud(roms.cpu, roms.text_chars())

    # each player's game: score, deaths, seconds carried into the next race, still in
    game = {'score': [0, 0], 'deaths': [0, 0], 'time': [0, 0], 'in': [True, False]}

    def begin_race(skip=False):
        """The arcade's race intro: each timer starts from what its player has left (0
        on a fresh race) and the race's seconds move onto it, 5 at a time.  skip: a test
        jump (F2, Shift+1-6) - the screen set up without the banner, the clocks running."""
        nonlocal time_left, state
        if hud is None:
            return
        hud.players = nplay
        for i in range(nplay):
            marble.set_score(i, game['score'][i]); marble.set_deaths(i, game['deaths'][i])
            if not game['in'][i]:
                marble.drop_player(i)
        if skip:
            secs = [RACE_TIME[race]] * nplay
            hud.race_start(race, secs, 0, game['score'][:nplay], game['in'][:nplay])
            hud.t.race_intro_end(); hud.phase = None
            for i in range(nplay):
                if game['in'][i]:
                    marble.set_clock(i, secs[i], not free_time)
            if snd is not None:
                snd.stop_all(); snd.threshold = 0
            sound(roms.L(0x1ef7a + 4 * race))
            ult_cycle['on'] = race == 5
            state = 'play'
            return
        base = [0 if race in FRESH_TIME else game['time'][i] for i in range(nplay)]
        hud.race_start(race, base, RACE_TIME[race], game['score'][:nplay], game['in'][:nplay],
                       winner=game.get('winner'))
        game['winner'] = None
        game['played'] = True                       # (0x4003E8, FUN_10504)
        if race == 0:
            sound(roms.L(0x1ef7a))                  # Practice: its music with the intro (0x1073C)
        state = 'intro'

    def new_game(n):
        """Start pressed with the credits for n players: Practice, fresh scores."""
        nonlocal nplay, race, level, field, relief, level_h, marble, cam
        nplay, race, cam = n, 0, 0.0
        sound(0x02); sound(0x00)                    # every sound again; all stopped (0x11076)
        game.update(score=[0, 0], deaths=[0, 0], time=[0, 0], **{'in': [True, n == 2]})
        level, field, relief, level_h, marble = start(race)
        begin_race()

    hiscores = mt.HighScores(roms.cpu, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   'marble_hiscores.json'))
    attract = {'mode': None, 'timer': 0, 'demos': 0, 'race': 1, 'entry': None, 'logo': None}

    def save_rng():
        if use_engine and hasattr(marble, 'm'):
            rng_state[0] = bytes(marble.m.ram[0x3a6:0x3a8])

    def to_attract(after_game=False):
        """Game over (or power on): first the initials of anyone whose score makes the
        table (FUN_11B18, player 1 first), then the high-score page."""
        nonlocal state, wait
        state, wait = 'attract', 0
        hud.players = 1
        queue = []
        if after_game:
            for i in range(nplay):
                if hiscores.rank(game['score'][i]) != 10:
                    queue.append(i)
        attract['queue'] = queue
        attract_next(150 if after_game else 300)

    def attract_next(hs_timer=300):
        """The next page of the attract loop (0x11452 by 0x400392)."""
        nonlocal level, field, relief, level_h, marble, race, nplay, cam
        save_rng()
        ult_cycle['on'] = False                           # (0x1147C: 0x400460 = 0xFF)
        if attract.get('queue'):
            i = attract['queue'].pop(0)
            set_sprite_colours(roms, 0)
            attract['mode'] = 'entry'
            hud.t.clear(); hud.t.normal_colours(); hud.timed = []
            mb.me.balls_start_18cd2(marble.m, 6, i)        # six balls in the player's colour
            attract['entry'] = hud.hs_entry(hiscores, i, nplay, game['score'][i], game.get('initials', ['AAA', 'AAA'])[i])
            attract['player'] = i
            if game.get('played'):
                sound(0x1a)                               # (0x11B70: not after the Ultimate)
            hud.entry_input = (0, 0, False)
            next(attract['entry'])
            return
        if attract['mode'] not in ('hs', 'credits'):
            set_sprite_colours(roms, 0)                   # (26B2A(0): race 0's colours)
            attract['mode'], attract['timer'] = 'hs', hs_timer
            hud.attract(credits, hiscores)
            mb.me.balls_start_18cd2(marble.m, 3, -2)        # three balls (0x115B8)
            if attract.pop('tune', False):                # after the credits page, with the
                sound(roms.L(0x1efaa + 4 * mb.me.random_13a98(marble.m, 3)))   # option on
            return
        attract['demos'] += 1                             # the page is over: a demo
        if attract['demos'] > 7 and attract['mode'] != 'credits':
            attract['demos'] = 0
            attract['mode'], attract['timer'] = 'credits', 200
            marble.m.wb(0x4003e2, 0)
            hud.credits_page()
            attract['tune'] = attract_music               # (0x4003E6, from 0x4003DC & 0x4000)
            return
        attract['race'] ^= 1
        race = attract['race']
        nplay = 1
        level, field, relief, level_h, marble = start(race)
        cam = 0.0
        m = marble.m
        m.ww(0x400390, 1)                                  # race state 1: the demo drives
        m.wl(0x400446, m.read32(0x1d364 + 4 * race))
        marble.set_clock(0, 60, True)
        attract['mode'] = 'demo'
        sound(0x01)                                  # only the loudest sounds (0x114C2)
        sound(roms.L(0x1ef7a + 4 * race))            # the demo race's music (FUN_10504)
        hud.demo_start(credits)

    def attract_frame():
        """One frame of the attract loop."""
        mode = attract['mode']
        if mode == 'entry':
            mb.me.balls_step_18dca(marble.m)
            try:
                next(attract['entry'])
            except StopIteration as e:
                if e.value:
                    game.setdefault('initials', ['AAA', 'AAA'])[attract['player']] = e.value[1]
                attract['mode'] = None
                attract_next(150)
            return
        if mode == 'demo':
            marble.step(0.0, 0.0)
            cap = getattr(marble.m, 'demo_caption', None)
            if cap:
                hud.caption(*cap); marble.m.demo_caption = None
            hud.tick_timed()
            hud.update([marble.score_of(0)], [max(0, marble.clock(0))])
            if marble.m.rw(0x40075a) == 1:                # the waypoints have run out
                attract['mode'] = 'between'
                attract['timer'] = 2                       # (0x400392 = 1: two frames)
            return
        if mode in ('hs', 'between', 'credits'):
            if mode == 'hs':
                mb.me.balls_step_18dca(marble.m)
                hud.attract_step()
            attract['timer'] -= 1
            if attract['timer'] <= 0:
                if mode == 'between':
                    attract['mode'] = 'demo-done'
                    attract_next(300)
                else:
                    attract_next()

    wait = 0                             # frames left on a game-over / congratulations page
    ending = None

    def end_sequence():
        """After the Ultimate Race (race state 6, ROM 0x113D0 / FUN_18A88): 28 bouncing
        balls in new colours, and for each player who finished: the end screen, the
        total counted into the score 250 every 2 frames, 90 frames; then 180 frames
        and the game is over."""
        m = marble.m
        sound(0x1b)                                   # (0x113F2)
        game['played'] = False                        # (0x4003E8 cleared)
        mb.me.balls_start_18cd2(m, 0x1c, -1)
        set_ball_colours(m.ball_palette)
        for i in range(nplay):
            if marble.status(i) != 3:
                continue
            total = hud.t.end_screen(i, nplay, marble.clock(i), marble.deaths_of(i))
            yield
            while total > 0:
                total -= 250
                marble.set_score(i, marble.score_of(i) + 250)
                hud.t.end_count(total, marble.score_of(i))
                yield
                yield
            for _ in range(90):
                yield
        for i in range(nplay):
            game['score'][i] = marble.score_of(i)
        for _ in range(180):
            yield
        m.wb(0x4003e2, 0)
    timeup = None

    def race_over_sounds():
        """Race state 3 (0x11332): the rolling sound stops; Aerial's 0x10 fades."""
        if hasattr(marble, 'm'):
            mb.me.stop_roll_15884(marble.m)
            if race == 3:
                marble.m.sound(0x11)

    def time_up_sequence():
        """FUN_16A20, a frame at a time: the OUT OF TIME / GAME OVER box for whoever
        ran out, 180 frames, the rows cleared; then, if one player is still racing,
        their READY, SET, GO! (60 frames each) and their clock restarted."""
        out, left = marble.time_up_begin()
        hud.t.time_up(nplay, out)
        for _ in range(180):
            yield
        hud.t.clear_rows()
        if len(left) == 1 and marble.status(left[0]) == 1:
            i = left[0]
            hud.t.ready(nplay, i)
            for word in (None, 'SET', 'GO'):
                if word:
                    hud.t.ready(nplay, i, word)
                for _ in range(60):
                    yield
            marble.time_up_restart(i)
            hud.t.clear_rows()
        marble.time_up_end(out, left)
        for i in out:
            game['in'][i] = False
    if arcade:                           # power on: straight into the first demo
        state = 'attract'
        attract['mode'] = 'hs'
        attract_next()
    else:
        begin_race()
    bonus = []                           # end-of-race bonus lines
    cam = 0.0
    show_heights = False
    debug, lift = False, 0
    debug_msg = ''
    snap = False
    free_time = '--free' in sys.argv     # F4, --free, or any use of F1: the race clock stops
    occl = None                          # F5: paused, with the whole-screen wall-mask overlay
    overlay = False
    msg_seen, msg_t = '', 0
    pygame.event.set_grab(mouse_ball); pygame.mouse.set_visible(not mouse_ball)

    running = True
    entry_start = False
    while running:
        mdx = mdy = 0
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                shift = e.mod & pygame.KMOD_SHIFT
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif hud is not None and e.key == pygame.K_5:      # coin
                    if credits < 99:
                        sound(0x41)                      # (the IRQ, 0x2888A: more credits)
                    credits = min(credits + 1, 99)
                    if state == 'attract' and attract['mode'] in ('hs', 'demo'):
                        hud.credits(credits, 0x2000 if attract['mode'] == 'hs' else 0)
                elif state == 'attract' and attract['mode'] == 'entry' and e.key in (pygame.K_1, pygame.K_2):
                    entry_start = True                           # (0x4003AC: next letter)
                elif state == 'attract' and attract['mode'] == 'entry':
                    pass
                elif state == 'attract' and e.key in (pygame.K_1, pygame.K_2):
                    n = 1 if e.key == pygame.K_1 else 2
                    if credits >= n:
                        credits -= n
                        save_rng()
                        marble.m.wb(0x4003e2, 0)
                        new_game(n)
                elif state == 'attract':
                    pass
                elif hud is not None and e.key == pygame.K_r and not shift:
                    pass                                         # (player 2's up)
                elif e.key == pygame.K_r:
                    if state == 'over':
                        race = 0
                        level, field, relief, level_h, marble = start(race)
                        time_left = float(RACE_TIME[race])
                    else:
                        marble.reset()
                    state, banner, cam = 'play', 120, 0.0
                    time_left = float(RACE_TIME[race]) if race in FRESH_TIME else time_left
                    if hud is not None and state != 'over':
                        game['in'] = [True, nplay == 2]
                    begin_race()
                elif e.key == pygame.K_LEFTBRACKET:
                    sensitivity = max(0.03, sensitivity / 1.25)
                elif e.key == pygame.K_RIGHTBRACKET:
                    sensitivity = min(1.0, sensitivity * 1.25)
                elif e.key == pygame.K_h:
                    show_heights = not show_heights
                elif e.key == pygame.K_F2:
                    race = (race + 1) % 6
                    level, field, relief, level_h, marble = start(race)
                    time_left = float(RACE_TIME[race])
                    state, banner, cam = 'play', 120, 0.0
                    begin_race(skip=True)
                    if debug and hasattr(marble, 'debug_move'):
                        lift = 0
                        marble.debug_move()
                elif e.key == pygame.K_F3:
                    overlay = not overlay
                    if overlay and not hasattr(marble, 'cover_columns'):
                        debug_msg = 'mask overlay unavailable'
                elif e.key == pygame.K_F12:
                    snap = True
                elif e.key == pygame.K_F7 and snd is not None:
                    snd.volume = 0.0 if snd.volume else 1.0
                    debug_msg = 'sound ' + ('on' if snd.volume else 'off')
                elif e.key == pygame.K_F6:             # raw AVI of the game picture (no HUD)
                    if avi is None:
                        os.makedirs('recordings', exist_ok=True)
                        avi = RawAvi(os.path.join('recordings', time.strftime('marble_%Y%m%d_%H%M%S.avi')), W, H)
                        debug_msg = 'recording ' + avi.base
                    else:
                        debug_msg = f'saved {avi.base} ({avi.frames_total} frames)'
                        avi.close(); avi = None
                elif e.key == pygame.K_F5:
                    if occl is None and hasattr(marble, 'occlusion_overlay'):
                        occl = occlusion_surface(pygame, marble.occlusion_overlay(), level.get_size())
                    else:
                        occl = None
                elif e.key == pygame.K_F4:
                    free_time = not free_time
                    debug_msg = 'clock stopped' if free_time else 'clock running'
                    if hasattr(marble, 'stop_clocks') and state == 'play':
                        marble.stop_clocks(free_time)
                elif e.key == pygame.K_F1:
                    free_time = True                  # debugging: the clock stays stopped
                    if hasattr(marble, 'stop_clocks'):
                        marble.stop_clocks(True)
                    if state == 'over':               # back in the race if time had run out
                        state, time_left = 'play', max(time_left, 1.0)
                    if hasattr(marble, 'debug_move'):
                        debug, lift = not debug, 0
                        if debug:
                            marble.debug_move()
                        debug_msg = ''
                    else:
                        debug_msg = 'debug mode unavailable'
                elif debug and e.key == pygame.K_p:
                    line = marble.debug_info()
                    print(line)
                    with open('debug_spots.txt', 'a') as fh:
                        fh.write(line + '\n')
                    debug_msg = 'logged to debug_spots.txt'
                elif debug and e.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN, pygame.K_HOME):
                    step = 8 if e.mod & pygame.KMOD_SHIFT else 1
                    lift = 0 if e.key == pygame.K_HOME else max(0, lift + (step if e.key == pygame.K_PAGEUP else -step))
                    marble.debug_move(0, 0, lift)
                elif debug and e.mod & pygame.KMOD_CTRL and e.key in (pygame.K_RIGHT, pygame.K_LEFT,
                                                                      pygame.K_DOWN, pygame.K_UP):
                    dx, dy = {pygame.K_RIGHT: (0, 1), pygame.K_LEFT: (0, -1),
                              pygame.K_DOWN: (1, 0), pygame.K_UP: (-1, 0)}[e.key]
                    marble.debug_move(dx, dy, lift)
                elif e.key == pygame.K_m:
                    mouse_ball = not mouse_ball
                    pygame.event.set_grab(mouse_ball); pygame.mouse.set_visible(not mouse_ball)
                    pygame.mouse.get_rel()
                elif pygame.K_1 <= e.key <= pygame.K_6 and (shift or hud is None):
                    race = e.key - pygame.K_1
                    level, field, relief, level_h, marble = start(race)
                    time_left = float(RACE_TIME[race])
                    state, banner, cam = 'play', 120, 0.0
                    begin_race(skip=True)
            elif e.type == pygame.MOUSEBUTTONDOWN and not mouse_ball:
                place(marble, field, e.pos[0] / SCALE - BORDER, e.pos[1] / SCALE + cam)
                if debug:
                    lift = 0
                    marble.debug_move()

        keys = pygame.key.get_pressed()
        if hud is not None:                    # the arcade: player 1 arrows, player 2 R F D G
            ax = keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]
            ay = keys[pygame.K_DOWN] - keys[pygame.K_UP]
        else:
            ax = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (keys[pygame.K_LEFT] or keys[pygame.K_a])
            ay = (keys[pygame.K_DOWN] or keys[pygame.K_s]) - (keys[pygame.K_UP] or keys[pygame.K_w])
        ax2 = keys[pygame.K_g] - keys[pygame.K_d]
        ay2 = keys[pygame.K_f] - keys[pygame.K_r]
        if state == 'attract' and attract['mode'] == 'entry':
            # the trackball counts the arcade reads (0x4006AA x, 0x4006A8 y): up = on through
            # the letters; either player's controls (FUN_180BE)
            tb_y = (ay or ay2) * mb.COUNTS_PER_INPUT
            if mouse_ball:
                tb_y += max(-8, min(8, int(mdy * sensitivity)))
            hud.entry_input = (0, int(tb_y), entry_start)
            entry_start = False
        if mouse_ball:
            # a trackball reports how far it was spun this frame: spin speed = push strength
            mdx, mdy = pygame.mouse.get_rel()
            ax += max(-4, min(4, mdx * sensitivity)); ay += max(-4, min(4, mdy * sensitivity))
        if occl is not None:
            pass                                       # F5: paused
        elif debug:
            # screen directions: right = x-1, y+1 (2 px right); down = x+1, y+1 (1 px down)
            if not pygame.key.get_mods() & pygame.KMOD_CTRL:
                kx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (keys[pygame.K_LEFT] or keys[pygame.K_a])
                ky = (keys[pygame.K_DOWN] or keys[pygame.K_s]) - (keys[pygame.K_UP] or keys[pygame.K_w])
                n = 4 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                if kx or ky:
                    marble.debug_move(n * (ky - kx), n * (ky + kx), lift)
        elif state == 'play' and replaying:
            if not marble.replay_step():
                state, debug_msg = 'replayed', 'end of the replay  (Esc quits)'
                if avi:
                    debug_msg += f'  - {avi.frames_total} frames recorded'
                    avi.close(); avi = None
        elif state == 'attract':
            attract_frame()
        elif state == 'won' and hud is not None:
            me_balls_step(marble)
            try:
                next(ending)
            except StopIteration:
                state, wait = 'over', 1
        elif state in ('over', 'won') and hud is not None and not replaying:   # (any start)
            wait -= 1
            if wait <= 0:
                to_attract(after_game=True)
        elif state == 'intro':
            v = hud.intro_step()
            if v is None:
                state, banner = 'play', 0
                if race:
                    sound(roms.L(0x1ef7a + 4 * race))   # the race's music (0x10F1C)
                for i in range(nplay):             # the clocks start (0x10F3C)
                    if game['in'][i]:
                        marble.set_clock(i, hud.timers[i], not free_time)
                if race == 3:
                    sound(0x43)                    # (0x10F8A)
                if race == 5:
                    ult_cycle['on'] = True         # (0x10F9E: 0x400460 cleared)
            else:
                time_left = float(v[0])
        elif state == 'timeup':
            try:
                next(timeup)
            except StopIteration:
                state = 'play' if marble.race_state != 4 else 'over'
                if state == 'over':
                    race_over_sounds()
                    wait = 5                       # (MAME: the initials 185 frames after
                                                   # the out-of-time jingle, the box is 180)
                    for i in range(nplay):
                        game['score'][i] = marble.score_of(i)
        elif state == 'play' and hud is not None:
            marble.step(float(ax), float(ay), float(ax2), float(ay2))
            time_left = float(marble.clock(0))
            racing = [i for i in range(nplay) if game['in'][i] and marble.status(i) in (1, 3)]
            done = [i for i in racing if marble.done(i)]
            out = []
            if marble.race_state == 4:              # somebody's time ran out (FUN_16A20)
                state, timeup = 'timeup', time_up_sequence()
            elif racing and len(done) == len(racing):
                state, banner = 'goal', 180
                race_over_sounds()
                bonus = []
                for i in racing:
                    game['in'][i] = i in done
                for i in done:
                    secs = min(marble.clock(i), 99)
                    # BONUS FOR TIME LEFT: 100 a second (0x11966) - after the Ultimate
                    # Race too; its own bonuses come on the end screen (FUN_18A88)
                    b = [('BONUS FOR TIME LEFT', secs * 100)]
                    marble.set_score(i, marble.score_of(i) + b[0][1])
                    if nplay == 2:
                        b = [(f'P{i + 1} ' + t, v) for t, v in b]
                    bonus += b
                    hud.update([marble.score_of(k) for k in range(nplay)],
                               [marble.clock(k) for k in range(nplay)])
                    hud.bonus(i, b[0][1])
            elif not racing:
                state, wait = 'over', 240
                for i in range(nplay):
                    game['score'][i] = marble.score_of(i)
        elif state == 'play':
            marble.step(float(ax), float(ay))
            if not free_time:
                time_left -= 1 / 60
            if goal_reached(race, marble, level_h):
                state, banner = 'goal', 180
                bonus = []
                if hasattr(marble, 'score'):
                    secs = min(int(time_left), 99)
                    if race < 5:                         # BONUS FOR TIME LEFT: 100 a second (0x11966)
                        bonus = [('BONUS FOR TIME LEFT', secs * 100)]
                    else:                                # the end of the game (0x18B6A)
                        died = min(marble.deaths, 20)
                        bonus = [('BONUS FOR FINISHING', 20000), (f'{secs} SEC LEFT x 1000', secs * 1000),
                                 (f'DIED {died} TIMES x -1000', -died * 1000)]
                    marble.score += max(0, sum(v for _, v in bonus))
                    if hud is not None and race < 5:
                        hud.update(marble.score, math.ceil(time_left))
                        hud.bonus(bonus[0][1])
            elif time_left <= 0:
                time_left, state = 0.0, 'over'
        elif state == 'goal':
            if hasattr(marble, 'finished'):
                marble.step(0.0, 0.0)                # the arcade marble rolls on into the goal
            banner -= 1
            if banner == 140:
                sound(roms.L(0x1ef92 + 4 * race))    # the race's music fades (0x11A14)
            if banner <= 0 and hud is not None:
                first = marble.m.rb(0x4003a4)         # first over the line: +5 s next race when
                game['winner'] = (first if nplay == 2 and all(game['in'][:2]) and first != 0xff
                                  else None)          # both are still in (ROM 0x107C0)
                for i in range(nplay):
                    game['score'][i], game['deaths'][i] = marble.score_of(i), marble.deaths_of(i)
                    game['time'][i] = marble.clock(i)
                if race == 5:
                    state, ending = 'won', end_sequence()
                else:
                    race += 1
                    level, field, relief, level_h, marble = start(race)
                    state, banner, cam = 'play', 120, 0.0
                    begin_race()
            elif banner <= 0:
                if race == 5:
                    state = 'won'
                else:
                    race += 1
                    carried = getattr(marble, 'score', 0), getattr(marble, 'deaths', 0)
                    level, field, relief, level_h, marble = start(race)
                    if hasattr(marble, 'score'):
                        marble.score, marble.deaths = carried   # score and deaths carry on
                    time_left = float(RACE_TIME[race]) if race in FRESH_TIME else time_left + RACE_TIME[race]
                    state, banner, cam = 'play', 120, 0.0
                    if hud is not None:
                        time_left -= RACE_TIME[race]
                        begin_race()

        if hasattr(marble, 'm'):                   # this frame's palette changes
            for n in marble.m.palettes:
                apply_palette(n)
            marble.m.palettes.clear()
        if ult_cycle['on']:                        # FUN_26D4E, every interrupt: the
            ult_cycle['count'] += 1                # Ultimate Race's colours, a step
            if ult_cycle['count'] > 6:             # every 7 frames through changes 12-17
                ult_cycle['count'] = 0
                ult_cycle['step'] = (ult_cycle['step'] + 1) % 6
                apply_palette(12 + ult_cycle['step'])
        frame_no[0] += 1
        if snd is not None:                        # this frame's sound commands
            if hasattr(marble, 'm'):
                for n in marble.m.sounds:
                    log_sound(n, 'engine')
                    snd.send(n)
                marble.m.sounds.clear()
            snd.update()

        mx, my = marble.screen_pos()
        if hasattr(marble, 'camera'):              # engine: the arcade's own scroll
            cam = float(marble.camera())
        else:
            target = max(0, min(level_h - H, my - H * 0.45))
            cam += (target - cam) * 0.15

        full.fill((0, 0, 0))
        ending_screen = state == 'won' and hud is not None
        amode = attract['mode'] if state == 'attract' else None
        demo = amode == 'demo'
        if (state == 'attract' and not demo) or ending_screen:
            pass                                 # (the text page on black)
        elif use_engine:                           # scenery the arcade redrew (FUN_1344C)
            draw_tile_patches(level, marble.playfield_patches(), tiles, level_pal, roms, race, level_idx)
        if (state != 'attract' or demo) and not ending_screen:
            view.blit(level, (0, -int(cam)))
        if amode == 'hs':                         # the title in the playfield (FUN_17044)
            view.blit(logo_surface(pygame, roms, tiles, logo_pal, attract), (64, 16))
        if show_heights:
            view.blit(relief, (0, -int(cam)))
        if not hasattr(marble, 'enemies_mo'):
            draw_enemies(pygame, view, marble, cam)     # (stand-in physics: placeholder balls)
        if state == 'attract' and not demo:
            if SPRITE_ART and amode in ('hs', 'entry'):
                draw_items(pygame, full, to_screen(marble.balls_mo()), 0, level)
        elif ending_screen:
            if SPRITE_ART:
                draw_items(pygame, full, to_screen(marble.balls_mo()), 0, level)
        elif SPRITE_ART and hasattr(marble, 'display_list'):
            draw_items(pygame, view, marble.display_list(), cam, level)   # the arcade's own order
        else:
            draw_hazards(pygame, view, marble, cam, level)
            draw_marble(pygame, view, marble, cam, level)
        if occl is not None:
            view.blit(occl, (0, -int(cam)))
            view.blit(font.render('F5 PAUSED: wall masks  white = from the wall heights, '
                                  'orange = hand-placed', True, (255, 255, 0)), (4, 18))
        if overlay and hasattr(marble, 'cover_columns'):
            draw_mask_overlay(pygame, view, marble, cam)

        if avi is not None and state in ('play', 'goal'):   # the picture before our own text
            avi.add(pygame.image.tobytes(view, 'RGB') if hasattr(pygame.image, 'tobytes')
                    else pygame.image.tostring(view, 'RGB'))
        if hud is not None:                        # the arcade's text layer, over everything
            if state in ('play', 'goal'):
                hud.update([marble.score_of(i) for i in range(nplay)],
                           [max(0, marble.clock(i)) for i in range(nplay)])
            full.blit(hud.surface(pygame), (0, 0))      # all 42 columns
            if free_time:
                view.blit(font.render('FREE', True, (255, 255, 255)), (W - 34, H - 14))
        else:
            if hasattr(marble, 'score'):
                view.blit(font.render(f'SCORE {marble.score:,}', True, (120, 160, 255)), (4, 16))
            col = (255, 80, 80) if time_left < 10 else (255, 255, 255)
            view.blit(font.render(f'{ml.RACES[race].upper()} RACE' + '',
                                  True, (255, 255, 255)), (4, 4))
            view.blit(font.render(f'{int(time_left):2d}' if not free_time else 'FREE', True, col),
                      (W - 22 if not free_time else W - 34, 4))
        if hud is None and state == 'play' and banner > 0:
            banner -= 1
            text = 'TIME TO FINISH' if race in FRESH_TIME else 'EXTRA TIME'
            centre(view, font, f'{text}  {RACE_TIME[race]} SEC', H // 2 - 20)
        if marble.broken and state == 'play':
            centre(view, font, 'OOPS!', H // 2, (255, 80, 80))
        if state == 'goal' and hud is None:
            centre(view, font, 'GOAL!', H // 2, (255, 255, 0))
            centre(view, font, f'{int(time_left)} SEC LEFT', H // 2 + 14)
            for i, (t, v) in enumerate(bonus):
                centre(view, font, f'{t}: {v:,}', H // 2 + 28 + 14 * i, (120, 160, 255))
        elif state == 'over' and hud is None:
            centre(view, font, 'OUT OF TIME', H // 2, (255, 80, 80))
            centre(view, font, 'GAME OVER' if arcade else 'R to start again', H // 2 + 14)
        elif state == 'won' and hud is None:
            centre(view, font, 'CONGRATULATIONS!', H // 2 - 28, (255, 80, 80))
            centre(view, font, 'YOU FINISHED THE ULTIMATE RACE!', H // 2 - 14, (255, 255, 0))
            for i, (t, v) in enumerate(bonus):
                centre(view, font, f'{t}: {v:,}', H // 2 + 2 + 14 * i, (120, 160, 255))
            if hasattr(marble, 'score'):
                for i in range(nplay):
                    who = f'PLAYER {i + 1} ' if nplay == 2 else ''
                    centre(view, font, f'{who}FINAL SCORE: {marble.score_of(i):,}', H // 2 + 50 + 14 * i,
                           (255, 140, 0))
        if debug_msg != msg_seen:                  # messages fade after 2 seconds
            msg_seen, msg_t = debug_msg, 120
        elif msg_t:
            msg_t -= 1
            if not msg_t:
                debug_msg = msg_seen = ''
        if debug:
            info = marble.debug_info()
            cut = info.index(' scroll')
            mk = info[info.index('[') + 1:-1].split()
            lines = ['DEBUG  F1 exit  P log  PgUp/PgDn lift ' + str(lift),
                     info[:cut], info[cut + 1:info.index(' masks')]]
            lines += ['masks ' + ' '.join(mk[i:i + 3]) for i in range(0, len(mk), 3)] or ['masks none']
            if debug_msg:
                lines.append(debug_msg)
            for i, t in enumerate(lines):
                view.blit(font.render(t, True, (255, 255, 0)), (4, H - 4 - 13 * (len(lines) - i)))
        elif debug_msg:
            view.blit(font.render(debug_msg, True, (255, 255, 0)), (4, H - 28))
        if mouse_ball:
            view.blit(font.render(f'trackball x{sensitivity:.2f}  (M release, [ ] adjust)', True, (200, 200, 200)), (4, H - 14))

        pygame.transform.scale(full, (FULL_W * SCALE, H * SCALE), screen)
        if avi is not None:                        # (on the window only, not in the recording)
            pygame.draw.circle(screen, (255, 0, 0), (FULL_W * SCALE - 14, 40), 7)
        pygame.display.flip()
        if snap:                                   # the frame exactly as shown
            snap = False
            os.makedirs('screenshots', exist_ok=True)
            name = os.path.join('screenshots', time.strftime('marble_%Y%m%d_%H%M%S')
                                + f'_{pygame.time.get_ticks() % 1000:03d}.png')
            pygame.image.save(screen, name)
            line = name
            if hasattr(marble, 'debug_info'):
                line += '  ' + marble.debug_info()
                with open('debug_spots.txt', 'a') as fh:
                    fh.write(line + '\n')
            print(line)
            debug_msg = 'saved ' + name
        clock.tick(60)
    if avi is not None:
        avi.close()
    pygame.quit()


def centre(view, font, text, y, colour=(255, 255, 255)):
    img = font.render(text, True, colour)
    view.blit(img, ((W - img.get_width()) // 2, y))


def draw_ball(pygame, view, pos, shadow, cam, colours, r=7):
    if shadow is not None:
        sh = pygame.Surface((2 * r + 4, r + 2), pygame.SRCALPHA)
        pygame.draw.ellipse(sh, (0, 0, 0, 110), (0, 0, 2 * r + 4, r + 2))
        view.blit(sh, (shadow[0] - r - 2, shadow[1] - cam - (r + 2) // 2))
    cx, cy = int(pos[0]), int(pos[1] - cam - r)
    dark, mid, light, glint = colours
    pygame.draw.circle(view, dark, (cx, cy), r)
    pygame.draw.circle(view, mid, (cx - 1, cy - 1), r - 1)
    pygame.draw.circle(view, light, (cx - 2, cy - 2), r // 2)
    pygame.draw.circle(view, glint, (cx - 3, cy - 3), 1)


STEELIE = ((0, 0, 0), (30, 30, 34), (70, 70, 80), (200, 200, 210))


def draw_enemies(pygame, view, marble, cam):
    """The steelies (placeholder black balls), when the arcade engine is running."""
    if hasattr(marble, 'enemies'):
        for pos, shadow in marble.enemies():
            draw_ball(pygame, view, pos, shadow, cam, STEELIE)


# Sprite colours, read off MAME's picture (the game sets the sprite palette at run time):
# the ball uses sprite palette 7, the band palette 6 (PROM 136033.119 entries 0x100 / 0x108).
BALL_COLOURS = {3: (0, 0, 67), 4: (0, 0, 254), 5: (84, 84, 254)}
BAND_COLOURS = {2: (203, 162, 0)}
SPRITE_ART = None


def make_sprite_art(pygame, mo):
    """Pygame surfaces for the arcade marble: the 16x16 ball (codes 5/6 left, 3/4 right)
    and the band segment tiles (codes 13..33 and 0x822.. -> tiles 13..0x44), plain and mirrored."""
    def surf(pix, colours):
        sf = pygame.Surface((pix.shape[1], pix.shape[0]), pygame.SRCALPHA)
        for yy in range(pix.shape[0]):
            for xx in range(pix.shape[1]):
                c = colours.get(int(pix[yy, xx]))
                if c:
                    sf.set_at((xx, yy), c)
        return sf
    import numpy as np
    ball = np.zeros((16, 16), np.uint8)
    ball[0:8, 0:8], ball[8:16, 0:8], ball[0:8, 8:16], ball[8:16, 8:16] = mo[5], mo[6], mo[3], mo[4]
    band = {}
    for t in range(0x0d, 0x45):
        band[(t, False)] = surf(mo[t], BAND_COLOURS)
        band[(t, True)] = surf(mo[t][:, ::-1], BAND_COLOURS)
    return {'ball': surf(ball, BALL_COLOURS), 'band': band, 'mo': mo, 'masks': {}}


def mask_surface(pygame, tile, h, flip):
    """White where the mask sprite has a pixel, transparent elsewhere (cached)."""
    key = (tile, h, flip)
    art = SPRITE_ART['masks'].get(key)
    if art is None:
        mo = SPRITE_ART['mo']
        art = pygame.Surface((8, 8 * h), pygame.SRCALPHA)
        for k in range(h):
            for yy in range(8):
                for xx in range(8):
                    if mo[(tile + k) & 0x7ff][yy, 7 - xx if flip else xx]:
                        art.set_at((xx, 8 * k + yy), (255, 255, 255, 255))
        SPRITE_ART['masks'][key] = art
    return art


def mo_surface(pygame, code, h, flip):
    """One arcade motion object (code -> tile and colour through the PROM lookup),
    h tiles tall, in the current race's sprite colours (cached)."""
    key = (code, h, flip)
    art = SPRITE_ART['objects'].get(key)
    if art is None:
        base, group = SPRITE_ART['lookup'][(code >> 8) & 0xff]
        tile = (base + (code & 0xff)) & 0x7ff
        pal = SPRITE_ART['palette'][group]
        mo = SPRITE_ART['mo']
        art = pygame.Surface((8, 8 * h), pygame.SRCALPHA)
        for k in range(h):
            for yy in range(8):
                for xx in range(8):
                    pen = int(mo[(tile + k) & 0x7ff][yy, 7 - xx if flip else xx])
                    if pen:
                        art.set_at((xx, 8 * k + yy), tuple(int(c) for c in pal[pen]))
        SPRITE_ART['objects'][key] = art
    return art


def logo_surface(pygame, roms, tiles, pal, cache):
    """The MARBLE MADNESS title: 6 rows x 20 playfield tiles from ROM 0x19F04 (copied by
    FUN_17044 to rows 2-7, columns 11-30), in the race palette of the moment."""
    key = id(pal)
    if cache.get('logo_key') != key:
        surf = pygame.Surface((160, 48))
        logo = roms.logo_words()
        for r in range(6):
            for c in range(20):
                v = int(logo[r, c])
                t = tiles[v & 0xfff]
                if v & 0x8000:
                    t = t[:, ::-1]
                rgb = pal[t + ((v >> 12) & 3) * 32]
                surf.blit(pygame.surfarray.make_surface(rgb.swapaxes(0, 1)), (c * 8, r * 8))
        cache['logo'], cache['logo_key'] = surf, key
    return cache['logo']


def me_balls_step(marble):
    """FUN_18DCA, every frame while there are balls (0x4003E2)."""
    mb.me.balls_step_18dca(marble.m)


def set_ball_colours(groups):
    """The ball colours FUN_26CFA put in palette RAM: sprite groups 0-7, pens 1-5."""
    if not SPRITE_ART:
        return
    pal = SPRITE_ART['palette'].copy()
    zt = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
    for g, words in groups.items():
        for k, w in enumerate(words):
            s_ = zt[w >> 12]
            pal[g, k + 1] = (((w >> 8) & 15) * s_, ((w >> 4) & 15) * s_, (w & 15) * s_)
    SPRITE_ART['palette'] = pal
    SPRITE_ART['objects'] = {}


def set_sprite_colours(roms, race):
    """Sprite colours change with the race (the ROM palette table is per race)."""
    if SPRITE_ART:
        SPRITE_ART['palette'] = roms.mo_palette(race)
        SPRITE_ART['lookup'] = roms.mo_lookup()
        SPRITE_ART['objects'] = {}


def occlusion_surface(pygame, items, size):
    """F5: every wall mask the arcade's hiding routine can use on this screen, half
    transparent: white from the wall heights, orange from the hand-placed table."""
    surf = pygame.Surface(size, pygame.SRCALPHA)
    for tile, h, flip, x, y, src in items:
        shape = mask_surface(pygame, tile, h, flip).copy()
        colour = (255, 255, 255, 128) if src == 'terrain' else (255, 140, 0, 128)
        shape.fill(colour, special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(shape, (x, y))
    return surf


def draw_tile_patches(level, patches, tiles, pal, roms=None, race=0, idx=None):
    """Draw tile blocks [(level row, column, width, height, codes)] into the level
    picture: each code like a level word (tile, colour bits 12-13, bit 15 mirror)."""
    if not patches:
        return
    import pygame
    for row, col, w, h, codes in patches:
        if isinstance(codes, tuple):                   # whole rows from the level data
            codes = [int(v) for v in ml.decode_alt_rows(roms, race, codes[1], row, h).flatten()]
        for k, v in enumerate(codes):
            r, c = row + k // w, col + k % w
            if not (0 <= c < 36 and 0 <= r * 8 < level.get_height()):
                continue
            t = tiles[v & 0xfff]
            if v & 0x8000:
                t = t[:, ::-1]
            pens = t + ((v >> 12) & 3) * 32
            if idx is not None:
                idx[r * 8:r * 8 + 8, c * 8:c * 8 + 8] = pens
            rgb = pal[pens]
            level.blit(pygame.surfarray.make_surface(rgb.swapaxes(0, 1)), (c * 8, r * 8))


def draw_hazards(pygame, view, marble, cam, level):
    """The hopping hazards (Beginner's acid blobs), from the arcade's sprite list."""
    if not SPRITE_ART or not hasattr(marble, 'hazards_mo'):
        return
    draw_items(pygame, view, marble.level_objects_mo() + marble.hazards_mo() + marble.slinkies_mo()
               + marble.popups_mo() + marble.enemies_mo(), cam, level)


def to_screen(items):
    """Sprite items from the game picture's coordinates to the whole screen's."""
    return [(c, x + BORDER, y, h, f, p) for c, x, y, h, f, p in items]


def draw_items(pygame, view, items, cam, level):
    """Motion objects [(code, x, y, h, mirrored, priority)] in level pixels, back to
    front; priority ones are wall masks (the level picture shows through them)."""
    for code, x, y, h, flip, prio in items:
        if prio:
            rect = pygame.Rect(x, y, 8, 8 * h).clip(level.get_rect())
            if rect.w <= 0 or rect.h <= 0:
                continue
            piece = level.subsurface(rect).convert_alpha()
            piece.blit(mask_surface(pygame, code & 0x7ff, h, flip), (x - rect.x, y - rect.y),
                       special_flags=pygame.BLEND_RGBA_MULT)
            view.blit(piece, (rect.x, rect.y - int(cam)))
        else:
            view.blit(mo_surface(pygame, code, h, flip), (x, y - int(cam)))


def draw_marble(pygame, view, marble, cam, level=None):
    """The arcade marble (engine mode) or a placeholder ball (stand-in physics)."""
    if marble.broken and not hasattr(marble, 'mo_list'):
        # shattering: hidden; reappearing at the checkpoint: blinking
        if getattr(marble, 'mode', 2) != 4 or (pygame.time.get_ticks() // 120) % 2:
            return
    if SPRITE_ART and hasattr(marble, 'mo_list'):
        # the arcade's own sprite list for the marble: ball and band, or the wobble /
        # break-up / broom frames, or the reappearing fragments; then the wall masks
        left, top = marble.ball_box()
        top -= int(cam)
        for code, dx, dy, h, flip, prio in marble.mo_list():
            if prio:
                if level is None:
                    continue
                tile = code & 0x7ff
                mx, my = left + dx, top + dy + int(cam)
                rect = pygame.Rect(mx, my, 8, 8 * h).clip(level.get_rect())
                if rect.w <= 0 or rect.h <= 0:
                    continue
                piece = level.subsurface(rect).convert_alpha()
                piece.blit(mask_surface(pygame, tile, h, flip), (mx - rect.x, my - rect.y),
                           special_flags=pygame.BLEND_RGBA_MULT)
                view.blit(piece, (rect.x, rect.y - int(cam)))
            else:
                view.blit(mo_surface(pygame, code, h, flip), (left + dx, top + dy))
        return
    r = 7
    sh = marble.shadow_pos()
    if sh is not None:
        shadow = pygame.Surface((2 * r + 4, r + 2), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (0, 0, 0, 110), (0, 0, 2 * r + 4, r + 2))
        view.blit(shadow, (sh[0] - r - 2, sh[1] - cam - (r + 2) // 2))
    x, y = marble.screen_pos()
    cx, cy = int(x), int(y - cam - r)
    pygame.draw.circle(view, (20, 40, 160), (cx, cy), r)
    pygame.draw.circle(view, (60, 110, 255), (cx - 1, cy - 1), r - 1)
    pygame.draw.circle(view, (140, 180, 255), (cx - 2, cy - 2), r // 2)
    pygame.draw.circle(view, (235, 245, 255), (cx - 3, cy - 3), 1)


OVERLAY_COLOURS = {('terrain', 'clear'): (0, 255, 0), ('table', 'clear'): (0, 255, 0),
                   ('terrain', 'mask'): (255, 255, 0), ('table', 'mask'): (255, 140, 0),
                   ('terrain', 'full'): (255, 0, 0), ('table', 'full'): (255, 0, 0)}


def draw_mask_overlay(pygame, view, marble, cam):
    """F3: what the arcade's wall check (FUN_264AA) decided for the marble."""
    left, top = marble.ball_box()
    top -= int(cam)
    cols = {dx: OVERLAY_COLOURS[(src, res)] for dx, src, res in marble.cover_columns()}
    tint = pygame.Surface(view.get_size(), pygame.SRCALPHA)
    for tile, h, flip, dx, dy in marble.masks():
        colour = cols.get(dx, (255, 0, 255))
        shape = mask_surface(pygame, tile, h, flip).copy()
        shape.fill((*colour, 140), special_flags=pygame.BLEND_RGBA_MULT)
        tint.blit(shape, (left + dx, top + dy))
    view.blit(tint, (0, 0))
    for dx, colour in cols.items():
        pygame.draw.rect(view, colour, (left + dx, top - 4, 8, 24), 1)
    hidden = marble.sprite() is None
    if hidden:
        for i in range(0, 16, 2):
            for px, py in ((left + i, top), (left + i, top + 15), (left, top + i), (left + 15, top + i)):
                view.set_at((px, py), (0, 255, 255))
    else:
        pygame.draw.rect(view, (0, 255, 255), (left, top, 16, 16), 1)


def place(marble, field, sx, sy):
    """Move the marble to the floor cell whose top is drawn nearest to (sx, sy)."""
    best = None
    for r in range(field.rows):
        for c in range(22 - (r & 1)):
            u = (r >> 1) + 21 - c
            v = r + 21 - u
            h = field.height(u + .5, v + .5)
            if h is None:
                continue
            x, y = field.to_screen(u + .5, v + .5, h)
            d = (x - sx) ** 2 + (y - sy) ** 2
            if best is None or d < best[0]:
                best = (d, u + .5, v + .5, h)
    if best:
        _, U, V, h = best
        if hasattr(marble, 'place'):                  # EngineMarble: the arcade code drops it on the floor
            marble.place(U, V)
            return
        marble.U, marble.V, marble.Z = U, V, h
        marble.vu = marble.vv = marble.vz = 0.0
        marble.airborne = False; marble.broken = 0

if __name__ == '__main__':
    main()
