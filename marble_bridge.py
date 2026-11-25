"""Bridge between the pygame front end and the ported arcade engine.

The arcade engine (marble_engine.py) is a set of routines operating on an
emulated copy of work RAM.  This module plays the part of the arcade's main
loop for the player's marble:

  * keeps a Machine with the marble record at 0x400018
  * starts the race with the ported race-start code (FUN_16EC6 chain, verified spawn)
  * turns mouse/keyboard movement into trackball axis counts (IN0/IN1), combines
    them at 45 degrees exactly as MAME does, and runs the arcade's trackball
    reader FUN_1AC18 every frame
  * every second frame (the arcade's physics runs at 30 Hz) runs the marble's
    per-state handler, FUN_253EC:
        state 0  normal       FUN_253BC -> FUN_17F66 -> FUN_121B8
        state 1  wobbling     same, then the impact meter (+0x56/+0x57) counts down
        state 5  (same idea)  same, then +0x56 counts down
        state 2  shattering   } animation scripts (FUN_25FC2, not ported yet):
        state 4  reappearing  } timed here with durations measured from real play
  * then runs the enemies (FUN_158CC: the steelies' steering, chasing and physics)
  * keeps a stand-in for the screen scroll, which decides when enemies appear
  * exposes the same interface as marble_physics.Marble so the game can use either

Everything the marble does on the terrain (slopes, walls, ledges, falls,
landings, breaking) is now the arcade's own code; the old stand-in physics is
only used for the drop shadow's floor height.
"""
import marble_engine as me

COUNTS_PER_INPUT = 9.0     # trackball counts per frame for one unit of game input (key held = 1):
                           # the user's MAME play spins at 9 counts per frame (median and maximum);
                           # at 6 the marble could hardly climb off Beginner's starting ramp
COUNT_CLAMP = 0x5f         # keep per-frame movement inside FUN_1AC18's plausible range
X_PER_CELL = 8.0           # game X/Y units per height-map cell
FIX = 65536.0              # 16.16 fixed point
U_OFF, V_OFF = 0.4, -1.1   # game X/Y -> height-map lattice (see marble_physics)

# durations (in 30 Hz updates) of the animation-driven states, from the user's MAME capture
CRUSHED_UPDATES = 64       # an enemy squashed by a landing marble (not seen in real play yet)


def tick_frames(ram):
    """The frames of a capture [(frame, work RAM)] that finish an arcade tick.  Usually
    the frame the tick counter (0x400010) moves; but a slow tick (a lot of playfield to
    redraw) runs on into the next frame - the counter has moved and the marble's block
    has not moved yet - and the trackball it reads is the frame it moves in."""
    out = set()
    blk = lambda r: r[0x18:0x18 + 0x18]          # the marble's velocity and position
    for i in range(1, len(ram)):
        f, r = ram[i]
        p = ram[i - 1][1]
        if r[0x10:0x14] == p[0x10:0x14]:
            continue
        done = f
        if blk(r) == blk(p):                 # nothing yet: the first later frame (before the
            j = i + 1                        # counter moves again) where the marble changes
            while j < len(ram) and ram[j][1][0x10:0x14] == r[0x10:0x14]:
                if blk(ram[j][1]) != blk(r):
                    done = ram[j][0]
                    break
                j += 1
        out.add(done)
    return out


class EngineMarble:
    """Drop-in replacement for marble_physics.Marble driven by the arcade routines."""

    def __init__(self, field, cpu_image, race=0, grid=None, players=1):
        self.f = field
        self.cpu, self.race, self.grid = cpu_image, race, grid
        self.players = players
        self.reset()

    # --- state ---------------------------------------------------------------------
    @property
    def mode(self): return self.m.rb(me.MARBLE + 0x1a)

    @property
    def broken(self): return self.mode in (2, 4)

    @property
    def airborne(self): return self.m.rb(me.MARBLE + 0x36) != 0

    def reset(self):
        self.m = me.Machine(self.cpu)
        self.frame = 0
        me.race_start(self.m, self.race, self.grid)          # arcade race start + spawn
        me.race_objects_start(self.m, self.players)          # player count, first scroll, enemies
        if self.players == 2:
            me.spawn_marble_259b4(self.m, 1)                 # the second marble at its own start
        self.crushed = {}
        self.ports = [[self.m.rb(a + 0xc9), self.m.rb(a + 0xc8)] for a in me.PLAYERS]
        self.fracs = [[0.0, 0.0], [0.0, 0.0]]          # sub-count trackball movement carried
        for a in me.PLAYERS:                                  # clocks stopped until the game starts them
            self.m.wb(a + 0x6a + 4, 0xff)

    def place(self, U, V):
        """Put the marble on the floor at height-map position U, V (mouse click)."""
        m, a2 = self.m, me.MARBLE
        saved = bytes(m.ram)
        m.wl(a2 + 0x0c, int((U - U_OFF) * X_PER_CELL) << 16)
        m.wl(a2 + 0x10, int((V - V_OFF) * X_PER_CELL) << 16)
        m.ww(0x400698, 0xffff); m.ww(0x400696, 0xffff)       # force a terrain refresh
        me.sprite_pos_update_1bab2(m, a2)
        floor = me.sprite_project_1cc62(m, 0)
        if me.s32(floor) < 0x1000 << 16:                      # no floor there (off the map)
            m.ram[:] = saved
            return
        m.wl(a2 + 0x14, floor)
        for o in (0, 4, 8):
            m.wl(a2 + o, 0)
        m.wb(a2 + 0x36, 0); m.wb(a2 + 0x1a, 0); m.wb(a2 + 0x56, 0); m.wb(a2 + 0x57, 0)
        me.helper_253bc(m, a2)                                # new last-safe tile / zone

    # --- debug: move the marble by hand, no physics -------------------------------------
    def debug_move(self, dx=0, dy=0, lift=0):
        """Shift the marble by (dx, dy) arcade units and sit it `lift` units above the
        floor there.  No velocity, friction or falling: only the drawing routines run
        (FUN_1B9CC screen position, FUN_1C014 band, FUN_1281C/264AA visibility and
        wall masks) plus the stand-in scroll, so the marble can be parked anywhere.
        Returns False (and does not move) where there is no floor."""
        m, a2 = self.m, me.MARBLE
        saved = bytes(m.ram)
        x = (me.s32(m.rl(a2 + 0x0c)) >> 16) + dx
        y = (me.s32(m.rl(a2 + 0x10)) >> 16) + dy
        m.wl(a2 + 0x0c, (x << 16) & 0xffffffff); m.wl(a2 + 0x10, (y << 16) & 0xffffffff)
        m.ww(0x400698, 0xffff); m.ww(0x400696, 0xffff)       # force a terrain refresh
        me.sprite_pos_update_1bab2(m, a2)
        floor = me.s32(me.sprite_project_1cc62(m, 0))
        if floor < 0x1000 << 16:                              # no floor there
            m.ram[:] = saved
            return False
        m.wl(a2 + 0x14, (floor + (lift << 16)) & 0xffffffff)
        for o in (0, 4, 8):                                   # no velocity
            m.wl(a2 + o, 0)
        m.wb(a2 + 0x36, 0); m.wb(a2 + 0x1a, 0); m.wb(a2 + 0x56, 0); m.wb(a2 + 0x57, 0)
        me.helper_253bc(m, a2)
        for k in range(400):                                  # let the arcade scroll catch up
            for o in (0x1e, 0x22, 0x26):                      # (it follows the screen height +0x20,
                m.wl(a2 + o, 0)                               #  so that is worked out first)
            me.screen_pos_1b9cc(m, a2, True)
            self._scroll()
            if k >= 8 and not m.rb(0x400008):
                break
        for o in (0x1e, 0x22, 0x26):                          # drop the anti-jitter history
            m.wl(a2 + o, 0)
        me.screen_pos_1b9cc(m, a2, True)
        me.stripe_1c014(m, a2)
        me.sprite_list_1281c(m, a2)
        return True

    def debug_info(self):
        """One line describing where the marble is and what the arcade does with it."""
        m, a2 = self.m, me.MARBLE
        x, y, z = (me.s32(m.rl(a2 + o)) >> 16 for o in (0x0c, 0x10, 0x14))
        sx, sy = me.s16(m.rw(a2 + 0x1e)), me.s16(m.rw(a2 + 0x20))
        masks = ' '.join(f'{m.rw(a2 + 0x38 + 6 * k):04X}@{me.s16(m.rw(a2 + 0x3a + 6 * k))},'
                         f'{me.s16(m.rw(a2 + 0x3c + 6 * k))}'
                         for k in range(5) if m.rw(a2 + 0x38 + 6 * k))
        return (f'race {self.race + 1} x {x} y {y} z {z:04X} screen {sx},{sy} '
                f'scroll {me.s16(m.rw(0x40097e))} visible {m.rb(a2 + 0x1c)} '
                f'zone {m.rb(a2 + 0x1d)} masks [{masks}]')

    # --- per player (0 or 1) ------------------------------------------------------
    def score_of(self, i):
        return me.s32(self.m.rl(me.PLAYERS[i] + 0xbc))

    def set_score(self, i, v):
        self.m.wl(me.PLAYERS[i] + 0xbc, v & 0xffffffff)

    def deaths_of(self, i):
        return self.m.rw(me.PLAYERS[i] + 0xd2)

    def set_deaths(self, i, v):
        self.m.ww(me.PLAYERS[i] + 0xd2, v & 0xffff)

    def status(self, i):
        """+0x18: 0 not playing, 1 racing, 2 out (time up), 3 finished."""
        return self.m.rb(me.PLAYERS[i] + 0x18)

    def drop_player(self, i):
        """A player no longer in the game (ran out of time in an earlier race)."""
        self.m.wb(me.PLAYERS[i] + 0x18, 0)
        self.m.wb(me.PLAYERS[i] + 0x1c, 0)                   # not drawn

    def mode_of(self, i):
        return me.s8(self.m.rb(me.PLAYERS[i] + 0x1a))

    def done(self, i):
        """Over the goal line (rolling in, state 6) or finished (+0x18 = 3)."""
        return self.mode_of(i) == 6 or self.status(i) == 3

    # --- a player's time runs out (FUN_16A20, run by the main loop in race state 4) --
    def time_up_begin(self):
        """Every clock stops (+0x6E kept in +0x6D).  Returns (players just out,
        players still in: racing or finished)."""
        m = self.m
        n = me.s16(m.rw(0x400396))
        out = [i for i in range(n) if self.status(i) == 2]
        left = [i for i in range(n) if self.status(i) in (1, 3)]
        race = m.rw(0x400394)
        if not left:                                         # nobody left: the music fades
            if race == 3:
                m.sound(0x0f); m.sound(0x11)
            elif race == 4:
                m.sound(0x17); m.sound(0x15)
            else:
                m.sound(m.read32(0x1ef92 + 4 * race))
        m.sound(0x3f)                                        # out of time
        for i in range(n):
            a = me.PLAYERS[i]
            m.wb(a + 0x6d, m.rb(a + 0x6e)); m.wb(a + 0x6e, 0xff)
            if self.status(i) == 2:
                me.stop_roll_15884(m)
                if m.rb(a + 0x36) == 2:
                    m.sound(0x46)
                if m.rb(a + 0x58) == 0x10:
                    m.sound(0x44)
        return out, left

    def time_up_restart(self, i):
        """After GO!: that player's clock runs again."""
        a = me.PLAYERS[i]
        self.m.wb(a + 0x6e, self.m.rb(a + 0x6d))

    def time_up_end(self, out, left):
        """The race carries on (0x400390 = 0) when somebody is left; in a two-player
        race the marble that ran out is dazed where it stands (on plain floor, not in
        the air) or taken off the course."""
        m = self.m
        if left:
            m.ww(0x400390, 0)
        if self.players == 2 and len(left) == 1:
            for i in out:
                a = me.PLAYERS[i]
                if (me.s8(m.rb(a + 0x1a)) == 0 and m.rb(a + 0x58) in (0, 0x10)
                        and not m.rb(a + 0x36)):
                    me.state_entry_25bae(m, a, 2)
                else:
                    m.wb(a + 0x18, 0)

    @property
    def race_state(self):
        """0x400390: 3 = every marble finished or out, 4 = someone's time ran out."""
        return self.m.rw(0x400390)

    @property
    def score(self):
        return me.s32(self.m.rl(me.MARBLE + 0xbc))

    @score.setter
    def score(self, v):
        self.m.wl(me.MARBLE + 0xbc, v & 0xffffffff)

    @property
    def deaths(self):
        return self.m.rw(me.MARBLE + 0xd2)

    @deaths.setter
    def deaths(self, v):
        self.m.ww(me.MARBLE + 0xd2, v & 0xffff)

    @property
    def finished(self):
        """Crossed the goal line (state 6, rolling in) or done (+0x18 = 3)."""
        return self.mode == 6 or self.m.rb(me.MARBLE + 0x18) == 3

    def sprite(self):
        """The arcade marble as the game draws it (FUN_1B9CC + FUN_1C014 + FUN_1281C):
        (left, top) of the 16x16 ball in level-picture coordinates, and the 4 band
        segments as (tile, h-flip, dx, dy) relative to the ball's top-left.
        Sprite X is screen X; sprite Y counts up from the bottom (measured against
        MAME's picture at the race start)."""
        m, a2 = self.m, me.MARBLE
        x, y, z = (me.s32(m.rl(a2 + o)) >> 16 for o in (0x0c, 0x10, 0x14))
        left = y - x + 128
        top = ((x + y) >> 1) - (z - 0x4000) - 100 + self.f.y_offset
        sx, sy = me.s16(m.rw(a2 + 0x1e)), me.s16(m.rw(a2 + 0x20))
        segs = []
        if self.mode not in (0, 6):          # wobbling / shattering / reappearing: the arcade
            return left, top, segs           # shows animation frames there (not ported yet)
        if m.rb(a2 + 0x1c) == 0:             # FUN_1281C/264AA: hidden (fully behind a wall)
            return None
        for k in range(4):
            o = a2 + 0xa4 + 6 * k
            code, gx, gy = m.rw(o), me.s16(m.rw(o + 2)), me.s16(m.rw(o + 4))
            segs.append((code & 0xff, bool(code & 0x8000), gx - sx + 8, sy - gy + 8))
        return left, top, segs

    def camera(self):
        """Level-picture row at the top of the arcade screen: the arcade's own scroll
        (0x40097C) less the race's starting scroll - the playfield Y scroll it gives the
        hardware (0x400000 -> 0x820000), 0 at the race start where MAME shows level
        row 0.  (HEIGHT_Y_OFFSET holds -(starting scroll + 0x3F10) per race.)"""
        return me.s32(self.m.rl(0x40097c)) + 0x3f10 + self.f.y_offset

    def ball_box(self, a2=me.MARBLE):
        """(left, top) of the 16x16 ball in level-picture coordinates, drawn or not."""
        m = self.m
        x, y, z = (me.s32(m.rl(a2 + o)) >> 16 for o in (0x0c, 0x10, 0x14))
        return y - x + 128, ((x + y) >> 1) - (z - 0x4000) - 100 + self.f.y_offset

    def cover_columns(self):
        """Debug: the 8-pixel screen columns FUN_264AA looked at last update, as
        (dx relative to the ball's left, source 'terrain'/'table', 'clear'/'mask'/'full')."""
        sx = me.s16(self.m.rw(me.MARBLE + 0x1e))
        return [(col * 8 - sx + 8, src, res) for col, src, res in getattr(self.m, 'cover_cols', [])]

    def mo_list(self, a2=me.MARBLE):
        """The marble's motion objects as the arcade's sprite builder (0x26F3E, player
        branch) writes them: [(code, dx, dy, h, mirrored, priority)], dx/dy = top-left
        relative to the 16x16 ball box.  Priority entries are the wall masks (they show
        the playfield); the rest are drawn in list order.  Code bits 8-15 still select
        the tile bank and colour (PROM lookup, done by the front end)."""
        m = self.m
        if not m.rb(a2 + 0x1c):
            return []
        sx, sy = me.s16(m.rw(a2 + 0x1e)), me.s16(m.rw(a2 + 0x20))
        mode = me.s8(m.rb(a2 + 0x1a))
        attr = (m.rb(a2 + 0x19) << 11) & 0xffff
        raw = []                                       # (code, X px, Y px, h, flip, priority)

        def word(code, xw, yw, prio=False):
            raw.append((code, (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1, bool(yw & 0x8000), prio))

        if mode not in (4, 10, 11, 9, 2, 1, 5, 8):
            word(5 | attr, ((sx + 16) << 5) & 0x3fe0, (((sy + 16) << 5) & 0x3fe0) | 1)
            word(3 | attr, ((sx + 24) << 5) & 0x3fe0, (((sy + 16) << 5) & 0x3fe0) | 1)
        if mode not in (2, 9, 1, 5):
            for k in range(4):
                o = a2 + 0xa4 + 6 * k
                code = m.rw(o)
                if not code:
                    break
                yw = (code & 0x8000) | ((code >> 11) & 7) | (((me.s16(m.rw(o + 4)) + 16) << 5) & 0x3fe0)
                word((code & 0x7ff) | attr, ((me.s16(m.rw(o + 2)) + 24) << 5) & 0x3fe0, yw)
        else:
            for code, xw, yw in me.frame_sprites_1a8d2(m, m.read32(m.rl(a2 + 0x5a)), sx + 24, sy + 16, attr):
                word(code, xw, yw)
        for k in range(5):
            o = a2 + 0x38 + 6 * k
            code = m.rw(o)
            if not code:
                break
            yw = (code & 0x8000) | ((code >> 11) & 7) | (((me.s16(m.rw(o + 4)) + 16) << 5) & 0x3fe0)
            prio = bool(code & 0x4000)
            word((code & 0x7ff) | (0 if prio else attr), ((me.s16(m.rw(o + 2)) + 24) << 5) & 0x3fe0, yw, prio)
        for flag, ptr in ((0x67, 0x62), (0xd1, 0xcc)):  # the broom; the mode-8 effect
            if m.rb(a2 + flag):
                for code, xw, yw in me.frame_sprites_1a8d2(m, m.read32(m.rl(a2 + ptr)), sx + 24, sy + 16, 0x1000):
                    word(code, xw, yw)
        if m.rb(a2 + 0xd8) and mode not in (4, 10, 11, 7, 2):   # the floating score (FUN_285B0)
            y = sy + 16 + (16 if mode == 6 else 0) + 16
            for code, xw, yw in me.frame_sprites_1a8d2(m, m.rl(a2 + 0xd4), sx + 24 + me.s8(m.rb(a2 + 0x68)), y, attr):
                word(code, xw, yw)
        out = []
        for code, X, Y, h, flip, prio in raw:
            dx = (X - (sx + 16) + 256) % 512 - 256
            dy = (sy + 32 - Y - 8 * h + 256) % 512 - 256
            out.append((code, dx, dy, h, flip, prio))
        return out

    def playfield_patches(self):
        """Tile blocks the arcade has drawn into the playfield since the last call
        (animated scenery: FUN_1344C): [(level tile row, column, width, height, codes)]."""
        out = getattr(self.m, 'pf_patches', [])
        self.m.pf_patches = []
        return out

    def level_objects_mo(self, only=None):
        """Level objects drawn with sprites, at their screen position +0x4E:
        type 3 (sprite builder 0x27A16, Beginner's wave machine): three free-running
        parts (0x40044A/4E/52, colour bits 0x3800) and its own frame (+0x42, 0x3000);
        type 5 (0x27DD0, the goal flags): its frame (+0x42, 0x1800).
        [(code, x, y, h, mirrored, priority)] in level pixels."""
        m = self.m
        cam = self.camera()
        out = []
        for o in me.LEVEL_SLOTS:
            if only is not None and o != only:
                continue
            t = m.rb(o + 0x1f)
            if not m.rb(o + 0x18) or t not in (3, 5, 6, 10, 11, 12, 13):
                continue
            sx, sy = me.s16(m.rw(o + 0x4e)), me.s16(m.rw(o + 0x50))
            words = []
            if t in (10, 11, 12, 13):                      # Aerial's objects
                x, y = sx + 24, sy + 16
                lo, hi, attr = {10: (-64, 0x120, 0x3000), 11: (-32, 0x100, 0x1800),
                                12: (-32, 0x100, 0x3800), 13: (-32, 0x100, 0x1800)}[t]
                if not lo < y < hi:
                    continue
                words = [(c, xw, yw, False) for c, xw, yw in
                         me.frame_sprites_1a8d2(m, m.read32(m.rl(o + 0x42)), x, y, attr)]
                if t in (11, 13):                          # the vacuums (0x27C16): two masks
                    flip = 0x8000 if t == 13 else 0
                    x -= 8; y -= 0x40 - (4 if t == 13 else 0)
                    for _ in range(2):
                        words.append((0x500, (x << 5) & 0x3fe0, ((y << 5) & 0x3fe0) | 6 | flip, True))
                        y += -4 if t == 13 else 4; x += 8
                for code, xw, yw, prio in words:
                    X, Y, h = (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1
                    top = (256 - Y - 8 * h + 256) % 512 - 256
                    out.append((code, X - 24, top + cam, h, bool(yw & 0x8000), prio))
                continue
            if t == 6:                                     # Intermediate's waves (0x27ED4)
                if not -64 < sy + 16 < 0x140:
                    continue
                frame = m.read32(m.rl(o + 0x42))
                words += me.frame_sprites_1a8d2(m, frame, sx + 24, sy + 16,
                                                0x3800 if frame >= 0x220ee else 0x2800)
                if m.rl(o + 0x46) == 0x2227a:              # this script also hides what is behind
                    k = (m.rl(o + 0x42) - m.rl(o + 0x46)) >> 2
                    d3 = m.romw(0x20f64 + 2 * k)
                    if d3 != 0xffff:
                        x, y = sx + 24 + 8, sy + 16 - 0x40 + me.s8(m.romb(0x20f92 + k))
                        h = (d3 >> 11) & 7
                        for _ in range(4):                 # four 8-px columns, stepping down
                            words.append((d3 & 0x7ff, (x << 5) & 0x3fe0, ((y << 5) & 0x3fe0) | h, True))
                            x += 8; y += 4
                for code, xw, yw, *p in words:
                    X, Y, h = (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1
                    top = (256 - Y - 8 * h + 256) % 512 - 256
                    out.append((code, X - 24, top + cam, h, bool(yw & 0x8000), bool(p and p[0])))
                continue
            if t == 3:                                     # the wave machine (0x27A16)
                if not -64 < sy + 16 < 0x160:
                    continue
                for ptr in (0x40044a, 0x40044e, 0x400452):
                    words += me.frame_sprites_1a8d2(m, m.read32(m.rl(ptr)), sx + 24, sy + 16, 0x3800)
                words += me.frame_sprites_1a8d2(m, m.read32(m.rl(o + 0x42)), sx + 24, sy + 16, 0x3000)
            else:                                          # the goal flags (0x27DD0)
                if not -64 < sy + 16 < 0x100:
                    continue
                words += me.frame_sprites_1a8d2(m, m.read32(m.rl(o + 0x42)), sx + 23, sy + 16, 0x1800)
            for code, xw, yw in words:
                X, Y, h = (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1
                top = (256 - Y - 8 * h + 256) % 512 - 256
                out.append((code, X - 24, top + cam, h, bool(yw & 0x8000), False))
        return out

    def occlusion_overlay(self):
        """Debug (F5): every wall mask the arcade's hiding routine can use on screen,
        as [(tile, h, mirrored, x, y, source)] in level pixels; source 'terrain' (from
        the wall heights) or 'table' (the ROM's hand-placed masks)."""
        cam = self.camera()
        out = []
        for code, cx, cy, src in me.occlusion_map(self.m):
            h = ((code >> 11) & 7) + 1
            Y = (cy + 16) & 0x1ff
            top = (256 - Y - 8 * h + 256) % 512 - 256
            out.append((code & 0x7ff, h, bool(code & 0x8000), cx, top + cam, src))
        return out

    def enemies_mo(self, only=None):
        """The steelies as the sprite builder draws them (0x277B6): the frame 0x21F36
        (crushed: its animation at +0x5A) at the screen position +0x1E (colour bits
        0x2800), then their wall masks (+0x38).  Hidden ones (+0x1C = 0) are skipped.
        [(code, x, y, h, mirrored, priority)] in level pixels."""
        m = self.m
        cam = self.camera()
        out = []
        for a in me.ENEMY_SLOTS:
            if only is not None and a != only:
                continue
            if not m.rb(a + 0x18) or not m.rb(a + 0x1c):
                continue
            sx, sy = me.s16(m.rw(a + 0x1e)), me.s16(m.rw(a + 0x20))
            frame = 0x21f36 if m.rb(a + 0x18) != 2 else m.read32(m.rl(a + 0x5a))
            words = [(c, xw, yw, False) for c, xw, yw in
                     me.frame_sprites_1a8d2(m, frame, sx + 24, sy + 16, 0x2800)]
            for k in range(5):
                o = a + 0x38 + 6 * k
                code = m.rw(o)
                if not code:
                    break
                yw = (code & 0x8000) | ((code >> 11) & 7) | (((me.s16(m.rw(o + 4)) + 16) << 5) & 0x3fe0)
                prio = bool(code & 0x4000)
                words.append(((code & 0x7ff) | (0 if prio else 0x2800),
                              ((me.s16(m.rw(o + 2)) + 24) << 5) & 0x3fe0, yw, prio))
            for code, xw, yw, prio in words:
                X, Y, h = (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1
                top = (256 - Y - 8 * h + 256) % 512 - 256
                out.append((code, X - 24, top + cam, h, bool(yw & 0x8000), prio))
        return out

    def hazards_mo(self, only=None):
        """The hopping hazards as the sprite builder draws them (0x27AC4): the current
        frame at the screen position +0x28 (colour bits 0x2000), then their wall
        masks (+0x2C).  [(code, x, y, h, mirrored, priority)] in level-picture pixels."""
        m = self.m
        cam = self.camera()
        out = []
        for a in me.HAZARD_SLOTS:
            if only is not None and a != only:
                continue
            if not m.rb(a + 0x18):
                continue
            sx, sy = me.s16(m.rw(a + 0x28)), me.s16(m.rw(a + 0x2a))
            if not -32 < sy + 16 < 0x100:
                continue
            words = [(c, xw, yw, False) for c, xw, yw in
                     me.frame_sprites_1a8d2(m, m.read32(m.rl(a + 0x58)), sx + 24, sy + 16, 0x2000)]
            for k in range(5):
                o = a + 0x2c + 6 * k
                code = m.rw(o)
                if not code:
                    break
                yw = (code & 0x8000) | ((code >> 11) & 7) | (((me.s16(m.rw(o + 4)) + 16) << 5) & 0x3fe0)
                words.append((code & 0x7ff, ((me.s16(m.rw(o + 2)) + 24) << 5) & 0x3fe0, yw, True))
            for code, xw, yw, prio in words:
                X, Y, h = (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1
                top = (256 - Y - 8 * h + 256) % 512 - 256
                out.append((code, X - 24, top + cam, h, bool(yw & 0x8000), prio))
        return out

    def slinkies_mo(self, only=None):
        """The 0x401482 objects (Intermediate's slinkies) as the sprite builder draws
        them (0x27BC0): the current frame (+0x3A) at the screen position +0x28,
        colour bits 0x3000; no masks."""
        m = self.m
        cam = self.camera()
        out = []
        for a in me.SLINKY_SLOTS:
            if only is not None and a != only:
                continue
            if not m.rb(a + 0x18):
                continue
            sx, sy = me.s16(m.rw(a + 0x28)) + 0x18, me.s16(m.rw(a + 0x2a)) + 0x10
            if not -48 < sy < 0x120:
                continue
            fr = m.read32(m.rl(a + 0x3a))
            if fr == 0xffffffff:
                continue
            for code, xw, yw in me.frame_sprites_1a8d2(m, fr, sx, sy, 0x3000):
                X, Y, h = (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1
                top = (256 - Y - 8 * h + 256) % 512 - 256
                out.append((code, X - 24, top + cam, h, bool(yw & 0x8000), False))
        return out

    def silly_mo(self, a):
        """The Silly Race's objects as the sprite builder draws them (0x28018 / 0x2806E /
        0x280C4 for the pit's kinds 7 / 8 / 9 in colours 0x2800 / 0x3000 / 0x2000;
        0x2811A for the dropped ones, 0x3800, then their wall masks at +0x26)."""
        m = self.m
        cam = self.camera()
        sx, sy = me.s16(m.rw(a + 0x20)) + 0x18, me.s16(m.rw(a + 0x22)) + 0x10
        if not -16 < sy < 0x100:
            return []
        drop = a in me.DROP_SLOTS
        attr = 0x3800 if drop else {7: 0x2800, 8: 0x3000}.get(m.rb(a + 0x25), 0x2000)
        words = [(c, xw, yw, False) for c, xw, yw in
                 me.frame_sprites_1a8d2(m, m.read32(m.rl(a + 0x1c)), sx, sy, attr)]
        if drop:
            for k in range(3):
                o = a + 0x26 + 6 * k
                code = m.rw(o)
                if not code:
                    break
                yw = (code & 0x8000) | ((code >> 11) & 7) | (((me.s16(m.rw(o + 4)) + 16) << 5) & 0x3fe0)
                words.append((code & 0x7ff, ((me.s16(m.rw(o + 2)) + 24) << 5) & 0x3fe0, yw, True))
        out = []
        for code, xw, yw, prio in words:
            X, Y, h = (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1
            top = (256 - Y - 8 * h + 256) % 512 - 256
            out.append((code, X - 24, top + cam, h, bool(yw & 0x8000), prio))
        return out

    def balls_mo(self):
        """The bouncing balls as the sprite builder draws them (0x27906): tile 1 in the
        ball's colour group, 16 pixels tall, at (x / 16 - 8, y / 16 + 16) in sprite
        units.  [(code, x, y, h, mirrored, priority)] in screen pixels."""
        m = self.m
        out = []
        for i in range(m.rb(0x4003e2)):
            a = me.BALLS + 10 * i
            x, y = me.s16(m.rw(a)) >> 4, (me.s16(m.rw(a + 2)) >> 4) + 0x10
            Y = (y << 5 & 0x3fe0) >> 5
            top = (256 - Y - 16 + 256) % 512 - 256
            for half, dx in ((1, -8), (3, 0)):            # left half (code 1), right half (3)
                X = ((x + dx) << 5 & 0x3fe0) >> 5
                out.append(((m.rw(a + 8) | half) & 0xffff, X - 24, top, 2, False, False))
        return out

    def popups_mo(self, only=None):
        """Aerial's pop-up blocks while they are up (on the display list, timer -1):
        frame +8 at the screen position +0x0C, colour bits 0x2000 (0x27E7C)."""
        m = self.m
        cam = self.camera()
        out = []
        if m.rw(0x400394) != 3 or not m.rb(0x400760):
            return out
        for a in me.POPUP_SLOTS:
            if only is not None and a != only:
                continue
            if me.s16(m.rw(a + 2)) != -1:
                continue
            sx, sy = me.s16(m.rw(a + 0x0c)) + 0x18, me.s16(m.rw(a + 0x0e)) + 0x10
            if not -64 < sy < 0x100:
                continue
            for code, xw, yw in me.frame_sprites_1a8d2(m, m.read32(m.rl(a + 8)), sx, sy, 0x2000):
                X, Y, h = (xw >> 5) & 0x1ff, (yw >> 5) & 0x1ff, (yw & 7) + 1
                top = (256 - Y - 8 * h + 256) % 512 - 256
                out.append((code, X - 24, top + cam, h, bool(yw & 0x8000), False))
        return out

    # --- the arcade's display list (0x4003BC): who is drawn in front of whom -----------
    # Every drawn object has an entry; each frame the sprite builder (0x26F3E) works out
    # a box for each (FUN_1B12A) and makes three passes of swaps between entries 1, 2
    # and 3 apart (FUN_1A7A8) with the depth test FUN_1A80A; new entries go in front of
    # the first entry they are behind (FUN_18E6C).  Later entries are drawn over earlier.
    def _entries(self):
        """{(kind, slot): (box, items)} for everything on screen now."""
        m = self.m
        out = {}

        def frame_box(fr, z_off, depth, a, raw=False):
            if fr == 0xffffffff and not raw:
                b4, b5, b6, b7 = -4, -4, 8, 8
            else:
                b4, b5, b6, b7 = (me.s8(m.read8((fr + k) & 0xffffff)) for k in (4, 5, 6, 7))
            x, y, z = me.s16(m.rw(a + 0x0c)), me.s16(m.rw(a + 0x10)), me.s16(m.rw(a + 0x14))
            return (x + b4, y + b5, z + z_off, b6, b7, depth)

        for a2 in me.PLAYERS[:me.s16(m.rw(0x400396))]:
            if not m.rb(a2 + 0x18):
                continue
            x, y, z = me.s16(m.rw(a2 + 0x0c)), me.s16(m.rw(a2 + 0x10)), me.s16(m.rw(a2 + 0x14))
            left, top = self.ball_box(a2)
            items = [(c, left + dx, top + dy, h, fl, p) for c, dx, dy, h, fl, p in self.mo_list(a2)]
            out[(1, a2)] = ((x - 3, y - 3, z + 1, 6, 6, 6), items)
        for a in me.ENEMY_SLOTS:
            if m.rb(a + 0x18):
                ex, ey, ez = me.s16(m.rw(a + 0x0c)), me.s16(m.rw(a + 0x10)), me.s16(m.rw(a + 0x14))
                out[(2, a)] = ((ex - 3, ey - 3, ez + 1, 6, 6, 6), self.enemies_mo(a))
        for a in me.HAZARD_SLOTS:
            if m.rb(a + 0x18):
                out[(4, a)] = (frame_box(m.read32(m.rl(a + 0x58)), 0, 16, a), self.hazards_mo(a))
        for a in me.SLINKY_SLOTS:
            if m.rb(a + 0x18):
                out[(14, a)] = (frame_box(m.read32(m.rl(a + 0x3a)), -8, 0, a), self.slinkies_mo(a))
        if m.rw(0x400394) == 3 and m.rb(0x400760):
            for a in me.POPUP_SLOTS:
                if me.s16(m.rw(a + 2)) == -1:
                    px, py = (me.s8(m.rb(a + 4)) << 3) + 2, (me.s8(m.rb(a + 5)) << 3) + 2
                    out[(0x29, a)] = ((px, py, me.s16(m.rw(a + 6)), 4, 4, 20), self.popups_mo(a))
        if m.rw(0x400394) == 4:                             # the Silly Race's own (FUN_1B12A)
            for a in me.PIT_SLOTS:
                if m.rb(a + 0x18):
                    flat = m.rb(a + 0x1a) == 2
                    box = frame_box(m.read32(m.rl(a + 0x1c)), -8 if flat else 0, 0 if flat else 6, a, raw=True)
                    out[(m.rb(a + 0x25), a)] = (box, self.silly_mo(a))
            for a in me.DROP_SLOTS:
                if m.rb(a + 0x18):
                    x, y, z = me.s16(m.rw(a + 0x0c)), me.s16(m.rw(a + 0x10)), me.s16(m.rw(a + 0x14))
                    box = (x, y, z, 0, 0, 0) if m.rb(a + 0x1a) == 2 else (x - 4, y - 4, z, 8, 8, 4)
                    out[(0x0f, a)] = (box, self.silly_mo(a))
        for o in me.LEVEL_SLOTS:
            t = m.rb(o + 0x18) and m.rb(o + 0x1e) != 1 and m.rb(o + 0x1f)   # (0x1295C: all but tile animations)
            if t not in (3, 5, 6, 10, 11, 12, 13):
                continue
            fr = m.read32(m.rl(o + 0x3e))
            if t == 6:
                b4, b6 = (me.s8(m.read8((fr + k) & 0xffffff)) for k in (4, 6))
                ox, oy, oz = me.s16(m.rw(o + 0x0c)), me.s16(m.rw(o + 0x10)), me.s16(m.rw(o + 0x14))
                box = (ox, oy + b4, oz, 32, b6, 40)
            elif t in (11, 12, 13) and fr == 0xffffffff:
                ox, oy, oz = me.s16(m.rw(o + 0x0c)), me.s16(m.rw(o + 0x10)), me.s16(m.rw(o + 0x14))
                box = (ox, oy, oz - 8, 8, 8, 0)
            else:
                zo, d = {3: (-16, 16), 5: (0, 24), 10: (-8, 0), 11: (-8, 24), 12: (0, 16), 13: (-8, 24)}[t]
                box = frame_box(fr, zo, d, o, raw=t in (3, 5, 10))   # (these read it even from the end marker)
            out[(t, o)] = (box, self.level_objects_mo(o))
        return out

    @staticmethod
    def _behind(X, Y):
        """FUN_1A80A: 1 if box X must be drawn after (in front of) box Y."""
        x0, y0, z0, w, d, h = X
        X1 = (x0 + w, y0 + d, z0 + h)
        u0, v0, s0, w2, d2, h2 = Y
        Y1 = (u0 + w2, v0 + d2, s0 + h2)
        if sum(X1) <= u0 + v0 + s0:
            return 0
        if sum(Y1) <= x0 + y0 + z0:
            return 1
        if v0 >= X1[1]:
            return 0
        if y0 >= Y1[1]:
            return 1
        if u0 >= X1[0]:
            return 0
        if x0 >= Y1[0]:
            return 1
        return 0 if s0 >= X1[2] else 1

    def display_list(self):
        """Everything drawn this frame, in the arcade's order (back to front):
        [(code, x, y, h, mirrored, priority)] in level pixels."""
        ents = self._entries()
        if not hasattr(self, 'dlist'):
            self.dlist = []
        self.dlist = [k for k in self.dlist if k in ents]
        for k in ents:                                     # newcomers (FUN_18E6C)
            if k in self.dlist:
                continue
            box = ents[k][0]
            for i, e in enumerate(self.dlist):
                if self._behind(ents[e][0], box):
                    self.dlist.insert(i, k)
                    break
            else:
                self.dlist.append(k)
        L = self.dlist
        for gap in (1, 2, 3):                              # FUN_1A7A8 x3
            for i in range(len(L) - gap):
                if self._behind(ents[L[i]][0], ents[L[i + gap]][0]):
                    L[i], L[i + gap] = L[i + gap], L[i]
        out = []
        for k in L:
            out += ents[k][1]
        return out

    def masks(self):
        """FUN_264AA's cover masks for the marble: (first tile, height in tiles, mirrored,
        dx, dy) relative to the ball's top-left.  Inside a mask the playfield is drawn over
        the marble (the sprite priority bit; the mask tiles use only pen 1)."""
        m, a2 = self.m, me.MARBLE
        sx, sy = me.s16(m.rw(a2 + 0x1e)), me.s16(m.rw(a2 + 0x20))
        out = []
        for k in range(5):
            o = a2 + 0x38 + 6 * k
            code = m.rw(o)
            if not code:
                continue
            cx, cy = me.s16(m.rw(o + 2)), me.s16(m.rw(o + 4))
            # sprite height = bits 11-13 + 1 (checked against MAME's sprite RAM: 0xFD40 is
            # drawn 8 tiles tall, the rail-end masks 0xCD68/0x4D91 only 2); tile = bits 0-10
            h = ((code >> 11) & 7) + 1
            out.append((code & 0x7ff, h, bool(code & 0x8000), cx - sx + 8, sy - cy + 16 - 8 * h))
        return out

    def enemies(self):
        """(screen position, shadow position) of each active enemy."""
        out = []
        for a in me.ENEMY_SLOTS:
            if self.m.rb(a + 0x18) == 1:
                x, y, z = (me.s32(self.m.rl(a + o)) / FIX for o in (0x0c, 0x10, 0x14))
                U, V = x / X_PER_CELL + U_OFF, y / X_PER_CELL + V_OFF
                h = self.f.height(U, V)
                out.append((self.f.to_screen(U, V, z), None if h is None else self.f.to_screen(U, V, h)))
        return out

    # --- position for drawing ------------------------------------------------------
    def _uvz(self):
        m, a2 = self.m, me.MARBLE
        x, y, z = (me.s32(m.rl(a2 + o)) / FIX for o in (0x0c, 0x10, 0x14))
        return x / X_PER_CELL + U_OFF, y / X_PER_CELL + V_OFF, z

    def screen_pos(self):
        return self.f.to_screen(*self._uvz())

    def shadow_pos(self):
        U, V, _ = self._uvz()
        h = self.f.height(U, V)
        return None if h is None else self.f.to_screen(U, V, h)

    # --- one 60 Hz frame -----------------------------------------------------------------
    # --- replaying a MAME capture (mm_capture.lua) --------------------------------------
    def load_replay(self, path):
        """Start from the first running-race snapshot of a capture and keep, per MAME
        frame, what the replay needs: the trackball counters both players' ports
        read, the arcade's tick counter (0x400010) and the random register.
        Returns the race number the capture is on."""
        import json
        snaps = json.load(open(path))['snapshots']
        ram = [(s['frame'], bytes.fromhex(s['workRam'])) for s in snaps]
        i0 = next(i for i, (f, r) in enumerate(ram) if r[0x390:0x392] == b'\0\0')
        s0 = snaps[i0]
        self.race = int.from_bytes(ram[i0][1][0x394:0x396], 'big')
        self.m = me.Machine(self.cpu)
        self.m.ram[:] = ram[i0][1]
        self.m.pf[:] = bytes.fromhex(s0['playfieldRam'])
        self.m.mo[:] = bytes.fromhex(s0['spriteRam'])
        self.m.slap_bank = s0['slapsticBank'] & 3
        self.crushed = {}
        self.replay = {f: (r[0x10:0x14], bytes(r[0x18 + 0xc6:0x18 + 0xca]), bytes(r[0xfa + 0xc6:0xfa + 0xca]),
                           r[0x3a6:0x3a8], r[0x390:0x392], bytes(r[0x18 + 0x6a:0x18 + 0x6f] + r[0xfa + 0x6a:0xfa + 0x6f]))
                       for f, r in ram[i0:]}
        self.replay_ticks = tick_frames(ram[i0:])
        self.replay_frame = ram[i0][0]
        self.replay_synced = False
        self.replay_clock = False
        return self.race

    def replay_step(self):
        """One MAME frame of the capture: its trackball counters, and an arcade tick
        whenever MAME's tick counter moved.  False once the capture (or the race) ends."""
        f = self.replay_frame
        nxt = self.replay.get(f + 1)
        if nxt is None or nxt[4] != b'\0\0':
            return False
        m = self.m
        m.ram[0x18 + 0xc6:0x18 + 0xca] = nxt[1]
        m.ram[0xfa + 0xc6:0xfa + 0xca] = nxt[2]
        self.replay_frame = f + 1
        me.clocks_28a96(m)                        # (MAME's frame: the clocks, then maybe a tick)
        m.ram[0x18 + 0x6a:0x18 + 0x6f] = nxt[5][:5]   # (MAME's clocks as recorded: a capture made
        m.ram[0xfa + 0x6a:0xfa + 0x6f] = nxt[5][5:]   #  with endless time has them held)
        if f + 1 in self.replay_ticks:
            if not self.replay_synced:                # MAME's pre-race pass of the main loop
                m.ram[0x3a6:0x3a8] = self.replay[f][3]
                self.replay_synced = True
            self._update()
        return True

    def step(self, ax=0.0, ay=0.0, ax2=0.0, ay2=0.0):
        """ax, ay = player 1's input in screen directions (key held = 1); ax2, ay2 the
        second player's.  One arcade frame: trackballs, clocks, and every second frame
        the game update."""
        # screen movement -> physical trackball axes (signs measured from real play:
        # IN0 increasing rolls the marble left, IN1 increasing rolls it down)
        for i, (x, y) in enumerate(((ax, ay), (ax2, ay2))):
            f = self.fracs[i]
            f[0] += -x * COUNTS_PER_INPUT
            f[1] += y * COUNTS_PER_INPUT
            d0 = max(-COUNT_CLAMP, min(COUNT_CLAMP, int(f[0])))
            d1 = max(-COUNT_CLAMP, min(COUNT_CLAMP, int(f[1])))
            f[0] -= int(f[0]); f[1] -= int(f[1])
            self.ports[i] = list(me.trackball_ports(self.ports[i][0], self.ports[i][1], d0, d1))
        me.read_trackball_1ac18(self.m, [tuple(p) for p in self.ports])   # every frame, as the arcade
        me.clocks_28a96(self.m)                   # the race clocks (every frame, FUN_28A96)
        self.frame += 1
        if self.frame & 1 == 0:
            self._update()                    # physics runs every second frame

    # --- the race clocks ----------------------------------------------------------
    def clock(self, player=0):
        return me.s16(self.m.rw(me.PLAYERS[player] + 0x6a))

    def set_clock(self, player, seconds, running=True):
        a = me.PLAYERS[player] + 0x6a
        self.m.ww(a, seconds & 0xffff); self.m.wb(a + 2, 9)
        self.m.wb(a + 4, 5 if running else 0xff)

    def stop_clocks(self, stop=True):
        """Stop every clock, or restart those of marbles still racing."""
        for i in range(self.players):
            a = me.PLAYERS[i] + 0x6a
            if stop:
                self.m.wb(a + 4, 0xff)
            elif self.status(i) == 1 and self.mode_of(i) != 8:
                self.m.wb(a + 4, 5)

    # --- one 30 Hz update: the player, then the enemies (as the arcade orders them) --------
    def _update(self, scroll=None):
        self._scroll(scroll)                     # FUN_13EE6 comes first in the arcade's tick
        self._player()
        m = self.m
        me.enemies_update_158cc(m)
        me.hazards_update_1493c(m)               # the hopping hazards (0x401302)
        me.slinkies_update_17230(m)              # the 0x401482 objects (slinkies)
        me.pit_update_1912c(m)                   # the Silly Race: the pit's creatures
        me.drops_update_19baa(m)                 # the Silly Race: the dropped ones
        me.popups_update_1844a(m)                # Aerial's pop-up blocks (0x401650)
        me.level_objects_update_12fd0(m)         # level objects + playfield animation patch
        for a in me.ENEMY_SLOTS:                     # crushed enemies: animation not ported, timed
            if m.rb(a + 0x18) == 2:
                self.crushed[a] = self.crushed.get(a, 0) + 1
                if self.crushed[a] >= CRUSHED_UPDATES:
                    m.wb(a + 0x18, 0); self.crushed.pop(a)
        c = me.s16(m.rw(0x400768))               # the main loop (0x1181E): after Aerial's 0x12
        if c >= 0:                               # (group 1's flag on), 0x3C passes and then
            m.ww(0x400768, c - 1)                # 0x13 turns it off again
            if c - 1 < 0:
                m.sound(0x13)
                m.wb(0x40076a, m.rb(0x40076a) | 4)
        me.random_13a98(m, 0x100)                # the main loop stirs the random numbers each pass (0x118A4)

    def _scroll(self, new=None):
        """The arcade's scroll routine FUN_13EE6 (first in the tick); `new` forces a
        scroll value instead (tests replaying MAME's own)."""
        m = self.m
        m.wl(0x400010, (m.rl(0x400010) + 1) & 0xffffffff)   # tick count (bumped by the interrupt)
        if new is None:
            me.scroll_13ee6(m)
            return
        me.playfield_animation_1344c(m)
        old = me.s32(m.rl(0x40097c))
        if new != old:
            m.wl(0x40097c, new & 0xffffffff)
            me.scroll_rows_144e4(m, old, new)

    # --- FUN_253EC, the marble's per-state handler -----------------------------------------
    def _player(self):
        """FUN_253EC for player 1: rolling, wobbling, breaking (with the broom),
        reappearing and the goal roll-in, as the arcade runs them."""
        me.players_251de(self.m)
