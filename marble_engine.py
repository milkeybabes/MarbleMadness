"""Faithful Python port of Marble Madness's marble physics.

The arcade's routines are ported one by one, operating on an emulated copy of
the game's work RAM (8 KB at 0x400000) so every value lives at the same
address as in the original.  Each routine is checked byte-for-byte against
marble-love's TypeScript port running on recorded MAME frames.

Ported from marble-love (github.com/magno73/marble-love), MIT License,
Copyright (c) 2026 Marco Magnocavallo, cross-checked against the 68010
disassembly of the user's own ROM.

Ported so far:
  step 1, core motion
    FUN_253BC  helper_253bc            object pre-step (cached screen coords)
    FUN_17F66  object_step_17f66       trackball -> velocity, gravity while airborne
    FUN_26196  flag_scaled_magnitude   picks the speed limit
    FUN_261BC  fun_261bc               speed limiter + rolling-animation counter
    FUN_28608  object_accum_flag_28608 animation step flag
  step 2, terrain under the marble
    FUN_1BAB2  sprite_pos_update_1bab2 whole-pixel position + tile, refresh terrain when the tile changes
    FUN_1CABA  terrain_sample_1caba    reads the packed terrain codes around the marble into 0x401C28
    FUN_1CC62  sprite_project_1cc62    floor height under the marble + slope pull (0x4006A4/A6)
    FUN_1C676  bracket_lerp_1c676      edge/wall heights around the marble cell
  step 4, FUN_121B8 leaf routines
    FUN_12886  swap_long_pair_12886    swap vx/vy (screen-bounds bounce)
    FUN_25E7C  vector_scale_25e7c      friction / surface drag by surface type
    FUN_25DF6  slope_pull_25df6        slope pull (0x4006A4/A6) applied to velocity
    FUN_1706C  position_update_1706c   nudges velocity along edges flagged by FUN_1C676
    FUN_1B5C2  wall_bounce_1b5c2       walls/edges: flips velocity away from a wall and restores position
  step 5, landing and breaking
    FUN_25C74  landing_25c74           landing: impact meter, hard landing -> dazed or broken
    FUN_25BAE  state_entry_25bae       enter state 2 (dazed), 9, or 4 (broken -> respawn)
    FUN_2591A  respawn_2591a           re-place the marble at the respawn point (globals 0x400462/66)
               (its helpers 0x262B2, 0x25B40, 0x1B9CC, 0x13966 are not ported yet)
  input
    FUN_1AC18  read_trackball_1ac18    per-frame trackball counters -> movement bytes (+0xC6/+0xC7)
    trackball_ports(...)               MAME's 45-degree combination of the two trackball axes
  step 3, race start
    FUN_16EC6  race_start              level pointers, SLAPSTIC pages, scroll start
    FUN_18FD0  fill_row_bases_18fd0    per-row base heights (run-length list, header +12)
    FUN_1A444  build_collision_1a444   height map -> 10-bit codes in the hidden playfield columns
               (uses marble_levels.build_heights, verified 100% against MAME)
    FUN_259B4  spawn_marble_259b4      marble start position from header +20/+22, dropped onto the floor
    FUN_11AC2  init_dynamic_cells_11ac2 starting codes for animated terrain cells (table at 0x40076E)
               + wave frame 0 patch (ROM 0x1699C: start index, count, then the codes);
                 the wave animation itself (table at 0x16658) is not ported yet
    FUN_180BE  pick_trackball_180be    one-player game: whichever trackball moved more steers
    FUN_1BC88  contact_1bc88           marble vs marble / enemy contact (swap velocities, push apart)
    FUN_160F6  edges_160f6             ledges: hanging over an edge, lift-off into a fall
               (the ROM keeps the smallest candidate from table 0x2398C; marble-love the largest)
"""

WORK = 0x400000
MARBLE = 0x400018          # the player's marble record (second player: 0x4000FA)


# ---------------------------------------------------------------------------
# machine state
# ---------------------------------------------------------------------------
class Machine:
    def __init__(self, cpu_image: bytes):
        self.rom = cpu_image                  # native address-indexed gameplay tables only (not program ROM)
        self.ram = bytearray(0x2000)          # work RAM 0x400000-0x401FFF
        self.pf = bytearray(0x2000)           # playfield RAM 0xA00000 (incl. hidden collision columns)
        self.mo = bytearray(0x1000)           # motion-object RAM 0xA02000
        self.alpha = bytearray(0x1000)
        self.color = bytearray(0x800)
        self.slap_bank = 0
        self.unported = set()                 # routines reached that are not ported yet
        self.sounds = []                      # sound commands sent this frame (FUN_158AC)
        self.palettes = []                    # palette changes queued (FUN_26B66)

    def sound(self, n):
        """FUN_158AC: send sound command n to the sound board, unless 0x4003B8 (the
        "no sounds" count) is set.  The game drains self.sounds once a frame."""
        if self.rw(0x4003b8) != 0:
            return 0
        self.sounds.append(n & 0xff)               # (only the low byte goes: move.b 7(a7))
        return 1

    def palette(self, n):
        """FUN_26B66: queue palette change n (table 0x20AE4); FUN_26B88 makes it in the
        next interrupt.  The game drains self.palettes once a frame."""
        self.palettes.append(n)

    # work RAM accessors (absolute 68k addresses) -----------------------------
    def rb(self, a): return self.ram[a - WORK]
    def rw(self, a): o = a - WORK; return (self.ram[o] << 8) | self.ram[o + 1]
    def rl(self, a):
        o = a - WORK
        return (self.ram[o] << 24) | (self.ram[o + 1] << 16) | (self.ram[o + 2] << 8) | self.ram[o + 3]
    def wb(self, a, v): self.ram[a - WORK] = v & 0xff
    def ww(self, a, v):
        o = a - WORK; v &= 0xffff
        self.ram[o] = v >> 8; self.ram[o + 1] = v & 0xff
    def wl(self, a, v):
        o = a - WORK; v &= 0xffffffff
        self.ram[o:o + 4] = v.to_bytes(4, 'big')

    # ROM accessors (0x80000-0x87FFF shows the current SLAPSTIC page) ----------------
    def romb(self, a):
        if 0x80000 <= a < 0x88000:
            return self.rom[0x80000 + self.slap_bank * 0x2000 + ((a - 0x80000) & 0x1fff)]
        return self.rom[a]
    def romw(self, a): return (self.romb(a) << 8) | self.romb(a + 1)

    # any address as the CPU sees it -----------------------------------------------
    def read8(self, a):
        a &= 0xffffffff
        if a < 0x88000: return self.romb(a)
        if WORK <= a < WORK + 0x2000: return self.ram[a - WORK]
        if 0xa00000 <= a < 0xa02000: return self.pf[a - 0xa00000]
        if 0xa02000 <= a < 0xa03000: return self.mo[a - 0xa02000]
        if 0xa03000 <= a < 0xa04000: return self.alpha[a - 0xa03000]
        return 0
    def read16(self, a): return (self.read8(a) << 8) | self.read8(a + 1)
    def read32(self, a): return (self.read16(a) << 16) | self.read16(a + 2)


# ---------------------------------------------------------------------------
# integer helpers (68k semantics)
# ---------------------------------------------------------------------------
def s8(v): v &= 0xff; return v - 0x100 if v & 0x80 else v
def s16(v): v &= 0xffff; return v - 0x10000 if v & 0x8000 else v
def s32(v): v &= 0xffffffff; return v - 0x100000000 if v & 0x80000000 else v
def muls_w(a, b): return s32(s16(a) * s16(b))                 # muls.w: word x word -> long


def asr(v, n): return s32(v) >> n                              # arithmetic shift right on a long


# ---------------------------------------------------------------------------
# FUN_253BC: cache screen coords before stepping
# ---------------------------------------------------------------------------
def helper_253bc(m, a2):
    if m.rb(a2 + 0x36):
        return
    m.ww(a2 + 0x32, asr(m.rl(a2 + 0x0c), 19))
    m.ww(a2 + 0x34, asr(m.rl(a2 + 0x10), 19))
    m.wl(a2 + 0x2a, m.rl(a2 + 0x14))
    m.wb(a2 + 0x1d, m.rb(a2 + 0x1b))


# ---------------------------------------------------------------------------
# FUN_17F66: trackball -> velocity; gravity while airborne
# ---------------------------------------------------------------------------
MOVE_COMMANDS = {0x00, 0x3b, 0x2d, 0x2e, 0x38, 0x39, 0x3a, 0x2f, 0x30, 0x31}
VEL_SCALE = 0x160          # muls.w #0x160 : velocity per trackball count
GRAVITY = -0x6000          # added to vz each update while airborne (-0.375 px)
TERMINAL_VZ = -0x50000     # fastest fall (-5 px per update)


def pick_trackball_180be(m):
    """FUN_180BE: in a one-player game both trackballs drive the marble; this
    frame's input comes from whichever moved more (|dx|+|dy|), player 1 on a tie."""
    p1, p2 = 0x400018, 0x4000fa
    mag = lambda a: (abs(s8(m.rb(a + 0xc6))) + abs(s8(m.rb(a + 0xc7)))) & 0xffff
    src = p1 if s16(mag(p1)) >= s16(mag(p2)) else p2
    m.wb(0x4006aa, m.rb(src + 0xc6))
    m.wb(0x4006a8, m.rb(src + 0xc7))


def object_step_17f66(m, a2):
    state18 = m.rb(a2 + 0x18)
    if state18 in (2, 3):
        return
    if m.rw(0x400390) == 1:                       # attract-mode demo driver
        demo_steer_1815a(m, a2)
        return
    cmd, state36 = m.rb(a2 + 0x58), m.rb(a2 + 0x36)
    if state36 != 2 and cmd in MOVE_COMMANDS:
        if m.rw(0x400396) == 1:
            pick_trackball_180be(m)               # one-player game: either trackball steers
        else:
            m.wb(0x4006aa, m.rb(a2 + 0xc6))       # trackball X for this object
            m.wb(0x4006a8, m.rb(a2 + 0xc7))       # trackball Y
        d3 = muls_w(s8(m.rb(0x4006a8)), VEL_SCALE)     # -> vx
        d2 = muls_w(-s8(m.rb(0x4006aa)), VEL_SCALE)    # -> vy
        mode = m.rb(a2 + 0x1a)
        if mode in (1, 5):
            d1 = s16(0x1f - s8(m.rb(a2 + 0x56)))
            if mode == 5 and d1 < 4:
                d1 = 4
            d3 = s32(muls_w(asr(d3, 8), d1) << 3)
            d2 = s32(muls_w(asr(d2, 8), d1) << 3)
        m.wl(a2 + 0x00, m.rl(a2 + 0x00) + d3)
        m.wl(a2 + 0x04, m.rl(a2 + 0x04) + d2)
    elif state36 != 0:
        vz = s32(m.rl(a2 + 0x08) + GRAVITY)
        m.wl(a2 + 0x08, max(vz, TERMINAL_VZ))
    flag_scaled_magnitude(m, a2)


# ---------------------------------------------------------------------------
# FUN_26196 / FUN_261BC: speed limit and rolling animation
# ---------------------------------------------------------------------------
SPEED_LIMIT = 0x40000      # 4.0 per update
SPEED_LIMIT_FAST = 0x50000 # 5.0 when record byte +0x1A is non-zero
ANGLE_TABLE = 0x1eef8


def flag_scaled_magnitude(m, a2):
    return fun_261bc(m, a2, SPEED_LIMIT_FAST if m.rb(a2 + 0x1a) else SPEED_LIMIT)


def fun_261bc(m, a2, limit):
    ax, ay = abs(s32(m.rl(a2))), abs(s32(m.rl(a2 + 4)))
    # |v| ~ max + 3/8 min   (word arithmetic as in the ROM)
    if ax > ay:
        speed = (((ay >> 3) & 0xffff) * 3 + ax) & 0xffffffff
    else:
        speed = (((ax >> 3) & 0xffff) * 3 + ay) & 0xffffffff
    if a2 in (0x400018, 0x4000fa):               # player marbles: rolling animation
        idx, frac = (speed >> 15) & 0xf, (speed >> 12) & 7
        w0 = m.romw(ANGLE_TABLE + 2 * idx)
        w1 = m.romw(ANGLE_TABLE + 2 * (idx + 1))
        acc = s32(s16(w0) + (s32(s16(w1 - w0) * frac) >> 3)) >> 9
        c4 = s16(m.rw(a2 + 0xc4) + s16(acc))
        m.ww(a2 + 0xc4, c4)
        if c4 > 0xa0:
            m.ww(a2 + 0xc4, c4 - 0xa0)
            object_accum_flag_28608(m, a2, 0xa)
    ret = limit
    if limit < speed:                            # too fast: scale velocity down
        divisor = (speed >> 8) & 0xffff
        q = ((limit << 6) // divisor) & 0xffff if divisor else 0
        vx = s32(muls_w(asr(m.rl(a2), 8), q)) >> 6
        m.wl(a2, vx)
        vy = s32(muls_w(asr(m.rl(a2 + 4), 8), q)) >> 6
        m.wl(a2 + 4, vy)
        ret = vy & 0xffffffff
    return ret


def object_accum_flag_28608(m, a2, value):
    m.wl(a2 + 0xbc, m.rl(a2 + 0xbc) + value)
    shift = m.rb(a2 + 0x19) & 0x3f
    m.wb(0x40039c, m.rb(0x40039c) | ((1 << shift) & 0xff))


# ---------------------------------------------------------------------------
# FUN_1BAB2: whole-pixel position + tile; refresh terrain sample on tile change
# ---------------------------------------------------------------------------
def derive_sprite_fields(m):
    x, y = m.rw(0x400690), m.rw(0x400692)
    m.ww(0x40069e, x & 7)                 # sub-tile fraction X
    m.ww(0x4006a0, y & 7)                 # sub-tile fraction Y
    m.ww(0x400696, s16(x) >> 3)           # tile X
    m.ww(0x400698, s16(y) >> 3)           # tile Y
    m.ww(0x4006a2, 0 if (y & 7) < (x & 7) else 1)   # which triangle of the cell


def sprite_pos_update_1bab2(m, a2):
    prev = (m.rw(0x400696), m.rw(0x400698))
    m.ww(0x400690, m.rw(a2 + 0x0c))       # integer parts of x, y, z
    m.ww(0x400692, m.rw(a2 + 0x10))
    m.ww(0x400694, m.rw(a2 + 0x14))
    derive_sprite_fields(m)
    if (m.rw(0x400696), m.rw(0x400698)) != prev:
        terrain_sample_1caba(m)


# ---------------------------------------------------------------------------
# FUN_1CABA: sample the packed terrain codes around the marble into 0x401C28
# ---------------------------------------------------------------------------
def terrain_sample_1caba(m):
    out = 0x401c28                          # 4 cells x 4 corner heights (words)
    lvl = m.rl(0x400474)                    # level header
    tile_x = m.rw(0x400696)
    d4 = (m.rw(0x400698) + 1 + tile_x - 0x15) & 0xffff
    d6 = (0x15 - tile_x + ((s16(d4) >> 1) & 0xffff)) & 0xffff
    a6 = 0x24b3a + (0x12 if s16(d4) & 1 else 0)
    col = 0x400478 + 2 * s16(d4)            # per-row base heights
    rows = m.read16(lvl + 0x18)
    for _ in range(4):
        if s16(d4) < 0 or s16(rows) <= s16(d4):
            for k in range(4): m.ww(out + 2 * k, 0)
            out += 8
        else:
            d0 = d6
            row_pair = (d4 & 0xff) & ~1
            if d4 & 1:
                d0 = (d0 + 0x16) & 0xffff
            a3 = (0xa00000 + s16(m.romw(0x1eb3a + row_pair))) & 0xffffffff
            a3 = (a3 + m.romb(0x1ed0a + d0) - 2) & 0xffffffff
            bits = m.read32(a3) >> (m.romb(0x1ed0a + 0x2c + d0) & 0x1f)
            idx = bits & 0x7fe                   # 10-bit dictionary index (x2)
            code = m.read16((m.rl(0x40065a) + idx) & 0xffffffff)
            while True:
                if code >= 0x1000:
                    v0 = ((code & 0x7f) - 0x40 + m.rw(col)) & 0xffff
                    if code >= 0xf000:              # flat cell
                        for k in range(4): m.ww(out + 2 * k, v0)
                    else:                           # two-level slope; bits 12-15 pick the high corners
                        coef = m.romw(0x1ed62 + ((s16(code) >> 6) & 0x3e))
                        alt = 0 if coef == 0x1000 else (-s16(coef) + s16(v0)) & 0xffff
                        for k in range(4):
                            m.ww(out + 2 * k, v0 if code & (1 << (12 + k)) else alt)
                    out += 8
                    break
                if code >= 0x800:                   # dynamic cell: look up the live code in RAM
                    code = m.rw(0x40076e + (code & 0x7fe))
                    continue
                if code == 0:                       # no floor
                    for k in range(4): m.ww(out + 2 * k, 0)
                    out += 8
                    break
                rec = (s16(code) + m.read32(lvl)) & 0xffffffff     # explicit corner bytes
                base = (m.rw(col) - 0x80) & 0xffff
                for k in range(4):
                    b = m.romb(rec + k)
                    m.ww(out + 2 * k, 0 if b == 0 else (b + base) & 0xffff)
                out += 8
                break
        st1, st2, st3 = m.romw(a6), m.romw(a6 + 2), m.romw(a6 + 4)
        a6 += 6
        d6 = (d6 + st1) & 0xffff
        d4 = (d4 + st2) & 0xffff
        col += 2 * s16(st2)
        out += s16(st3)


# ---------------------------------------------------------------------------
# FUN_1CC62: floor height under the marble, and the slope pull
# ---------------------------------------------------------------------------
def sprite_project_1cc62(m, arg=0):
    if arg & 0xff:
        terrain_sample_1caba(m)
    S = 0x401c28
    cx0, cx1, cy0, cz = m.rw(S + 0x04), m.rw(S + 0x0e), m.rw(S + 0x10), m.rw(S + 0x1a)
    if m.rw(0x4006a2):
        dx, dy = (cx1 - cx0) & 0xffff, (cx0 - cz) & 0xffff
    else:
        dx, dy = (cy0 - cz) & 0xffff, (cx1 - cy0) & 0xffff
    m.ww(0x4006a4, dx)                      # slope pull X
    m.ww(0x4006a6, dy)                      # slope pull Y
    d1 = s32(s16(dy) * s16(m.rw(0x4006a0)) + s16(dx) * s16(m.rw(0x40069e)))
    return s32(s32(s16(cz) << 16) + s32(d1 << 13))   # height, 16.16


# ---------------------------------------------------------------------------
# FUN_1C676: edge heights around the marble's cell (walls and drops)
# ---------------------------------------------------------------------------
def _bracket(m, out, dirw, key, hi, tie1, tie2, lo, hi2, probe, dlo, pivot, h1, l1, h3, l3, factor):
    if key == hi and tie1 == tie2:
        return
    if s16(key) < s16(hi) or s16(probe) < s16(dlo):
        d, v = 1, lo
    else:
        d, v = 3, hi2
    m.ww(out, v)
    if pivot == hi:
        d = (d + 1) & 0xff
    m.wb(dirw, d)
    if d in (1, 3):
        a, b = (h1, l1) if d == 1 else (h3, l3)
        diff = s16(s16(a) - s16(b))
        delta = s32(s32(diff * s16(factor)) + 4) >> 3
        m.ww(out, m.rw(out) + delta)


def bracket_lerp_1c676(m):
    for a in (0x40066a, 0x400672, 0x400670, 0x40066e, 0x40066c):
        m.wb(a, 0)
    s = [[m.rw(0x401c28 + 8 * c + 2 * k) for k in range(4)] for c in range(4)]
    (a0, a2_, a4, a6), (b0, b2, b4, b6), (c0, c2, c4, c6), (e0, e2, e4, e6) = s
    fa, fb = m.rw(0x4006a0), m.rw(0x40069e)
    _bracket(m, 0x400674, 0x40066c, a4, a6, e2, e0, e0, e2, e2, e0, e0, a6, e0, a4, e2, fa)
    _bracket(m, 0x400676, 0x40066e, a4, a2_, b6, b0, a2_, a4, b6, b0, b0, b0, a2_, b6, a4, fb)
    _bracket(m, 0x400678, 0x400670, c0, c2, b6, b4, b4, b6, b6, b4, b4, b4, c2, b6, c0, fa)
    _bracket(m, 0x40067a, 0x400672, c0, c6, e2, e4, c6, c0, e2, e4, e4, c6, e4, c0, e2, fb)
    lt = lambda p, q: s16(p) < s16(q)
    checks = [
        (0x01, 0x40067c, a0, (a4, a6, a2_)), (0x02, 0x40067e, b2, (b6, b0, b4)),
        (0x04, 0x400680, c4, (c0, c2, c6)), (0x08, 0x400682, e6, (e2, e4, e0)),
        (0x10, 0x40067c, a4, (a0, a6, a2_)), (0x20, 0x40067e, b6, (b2, b0, b4)),
        (0x40, 0x400680, c0, (c4, c2, c6)), (0x80, 0x400682, e2, (e6, e4, e0)),
    ]
    for bit, out, peak, others in checks:          # a corner higher than the other three
        if all(lt(o, peak) for o in others):
            m.wb(0x40066a, m.rb(0x40066a) | bit)
            m.ww(out, peak)
    base = m.rw(0x400694)
    for out in range(0x400674, 0x400684, 2):
        m.ww(out, m.rw(out) - base)


# ---------------------------------------------------------------------------
# Race start: FUN_16EC6, FUN_18FD0, FUN_1A444, FUN_259B4
# ---------------------------------------------------------------------------
def fill_row_bases_18fd0(m):
    """Copy the native per-race row bases, not a protected ROM stream."""
    import marble_levels as levels
    for i, word in enumerate(levels.row_bases(m.rw(0x400394))):
        m.ww(0x400478 + 2*i, int(word))


def _dict_index(key, words):
    """FUN_1ABD4: binary search (unsigned words), step 0x400 bytes halving."""
    a2, step, hi = 0, 0x400, (len(words) - 1) * 2
    for _ in range(40):
        v = words[(a2 & ~1) // 2]
        if key == v:
            return a2 >> 1
        a2 = min(max(a2 - step if key < v else a2 + step, 0), hi)
        step >>= 1
    return 0x3ff


def _classify_cell(m, cs, d7, corner_list):
    """FUN_1AA38 for one cell: 4 corner heights -> terrain code."""
    a3, a4, a5, a6 = (s16(int(x)) for x in cs)       # (plain ints: numpy int16 shifts would wrap)
    if a3 != 0 and a3 < 0x1000:
        return a3                                       # special code from the patch list
    lo, hi = s16(d7 - 0x40), s16(d7 + 0x40)
    if all(c == 0 or lo <= c < hi for c in (a3, a4, a5, a6)):
        if a3 == a4 == a5 == a6:
            return 0 if a3 == 0 else (a3 - d7 + 0xf040) & 0xffff
        d4 = a3
        d5 = a4 if a4 != d4 else a5 if a5 != d4 else a6
        if all(x in (d4, d5) for x in (a5, a6)):
            if d4 > d5:
                d0, d3 = d4, (0x1000 if d5 == 0 else d4 - d5)
            else:
                d0, d3 = d5, (0x1000 if d4 == 0 else d5 - d4)
            ok = True
            if d3 == 0x1000:
                d3 = 31
            elif d3 > 0x60:
                ok = False
            else:
                d3 = s16(m.romw(0x1ec28 + 2 * d3)); ok = d3 >= 0
            if ok:
                d1 = (d0 - d7 + 0x40 + (d3 << 7)) & 0xffff
                for corner, bit in ((a4, 0x1000), (a5, 0x2000), (a6, 0x4000), (a3, 0x8000)):
                    if d0 == corner:
                        d1 = (d1 + bit) & 0xffff
                return d1
    adj = (0x80 - d7) & 0xffff                          # fallback: explicit corner list
    c3, c4, c5, c6 = [(x + adj) & 0xffff if x else 0 for x in (a3, a4, a5, a6)]
    key = ((((c4 << 8) + c5) & 0xffff) << 16) | (((c6 << 8) + c3) & 0xffff)
    p = 0
    while p <= 0x1000:
        v = m.read32(corner_list + p); p += 4
        if v == 0xffffffff or m.read32(corner_list + p) == key:
            break
    return p & 0xffff


def build_collision_1a444(m, race, grid):
    """Install native preclassified collision cells instead of rebuilding them
    with Atari's SLAPSTIC-protected table representations at every race start."""
    import marble_levels as levels
    pf, mo = levels.collision_map(race)
    m.pf[:] = pf.tobytes()
    m.mo[:] = mo.tobytes()


def race_start(m, race, grid):
    """FUN_16EC6 (+ the terrain build and marble spawn it leads to)."""
    m.ww(0x400394, race)
    hdr = (m.romw(0x2be00 + 4 * race) << 16) | m.romw(0x2be02 + 4 * race)
    m.wl(0x400474, hdr)
    m.ww(0x400662, m.romw(0x239a0 + 2 * race))      # SLAPSTIC page for the height data
    m.ww(0x400664, m.romw(0x239ac + 2 * race))      # SLAPSTIC page for the graphics rows
    m.slap_bank = m.rw(0x400662) & 3
    fill_row_bases_18fd0(m)
    m.wl(0x40065a, m.read32(hdr + 38))               # terrain dictionary
    m.wl(0x40065e, m.rl(0x40065a) + 2 * m.read16(hdr + 36) - 2)
    scroll = s16(m.read16(hdr + 16))
    if race == 4:
        scroll += s16(m.read16(hdr + 18))
    m.wl(0x40097c, scroll)
    build_collision_1a444(m, race, grid)
    init_dynamic_cells_11ac2(m)                     # (the ROM does this for Intermediate only;
    if race != 2:                                   #  elsewhere the wave's first frame comes
        apply_cell_patch(m, 0x1699c)                #  soon enough - this keeps it flat from the start)
    spawn_marble_259b4(m, 0)


def init_dynamic_cells_11ac2(m):
    """Copy the 66 starting codes for animated cells (waves, moving floors) into 0x40076E."""
    for i in range(66):
        m.ww(0x40076e + 2 * i, m.romw(0x1d370 + 2 * i))


def apply_cell_patch(m, addr):
    """A dynamic-cell patch list: start index, count, then that many codes."""
    start, n = m.romw(addr), m.romw(addr + 2)
    for i in range(n):
        m.ww(0x40076e + 2 * (start + i), m.romw(addr + 4 + 2 * i))


def spawn_marble_259b4(m, player=0):
    a2 = MARBLE if player == 0 else 0x4000fa
    w = m.read16(m.rl(0x400474) + 20 + 2 * player)
    m.wl(a2 + 0x0c, (((w >> 8) & 0xff) << 19) + 0x40000)     # cell centre, 16.16
    m.wl(a2 + 0x10, ((w & 0xff) << 19) + 0x40000)
    m.ww(0x400698, 0xffff); m.ww(0x400696, 0xffff)            # force a terrain refresh
    sprite_pos_update_1bab2(m, a2)
    m.wl(a2 + 0x14, sprite_project_1cc62(m, 0))               # drop onto the floor
    m.ww(a2 + 0xc4, 0); m.wb(a2 + 0x1b, 0)
    for o in (0, 4, 8):                                       # no velocity (ROM 0x25A64..)
        m.wl(a2 + o, 0)
    m.wb(a2 + 0x57, 0); m.wb(a2 + 0x56, 0); m.wb(a2 + 0x1a, 0)
    m.wb(a2 + 0x18, 1)                                        # in the race (ROM 0x25A7E)
    m.wl(a2 + 0x5a, 0); m.wb(a2 + 0x36, 0)
    for o in (0x58, 0xcb, 0xd8, 0x67, 0xd1):                  # (ROM 0x25A8C..)
        m.wb(a2 + o, 0)
    m.wl(a2 + 0x26, 0); m.wl(a2 + 0x22, 0)
    reset_orientation_25b40(m, a2)
    screen_pos_1b9cc(m, a2)
    stripe_1c014(m, a2)


def reset_orientation_25b40(m, a2):
    """FUN_25B40: the stripe's starting pose (tables 0x1D3F4 / 0x1D3FC, x2048, z = 0)."""
    m.wb(a2 + 0xca, 0)
    for k in range(8):
        m.ww(a2 + 0x74 + 2 * k, s8(m.romb(0x1d3f4 + k)) << 11)
        m.ww(a2 + 0x84 + 2 * k, s8(m.romb(0x1d3fc + k)) << 11)
        m.ww(a2 + 0x94 + 2 * k, 0)


# ---------------------------------------------------------------------------
# Input: FUN_1AC18 trackball reader (runs every frame)
# ---------------------------------------------------------------------------
def trackball_ports(port_a, port_b, d_in0, d_in1):
    """MAME's view of Marble Madness's 45-degree trackball: given last frame's
    counters and this frame's movement on the two physical axes, return the new
    8-bit counters the game reads at 0xF20001 / 0xF20003."""
    return (port_a + d_in0 + d_in1) & 0xff, (port_b + d_in0 - d_in1) & 0xff


def _track_axis(m, a0, port, prev_off, out_off):
    d = (port - m.rb(a0 + prev_off)) & 0xff        # movement since last frame (8-bit wrap)
    m.wb(a0 + prev_off, port)
    if s8(d) > 0x60 or s8(d) < -0x60:              # implausible jump: wrap-around or glitch
        if (m.rb(a0 + out_off) ^ d) & 0x80:          # direction flipped vs last movement
            d = 0x80 if s8(d) >= 0 else 0x7f         # clamp to the extreme in the old direction
    m.wb(a0 + out_off, d)


def read_trackball_1ac18(m, ports):
    """ports = [(port_a, port_b) for player 1, (port_a, port_b) for player 2]."""
    for player, (pa, pb) in enumerate(ports[:2]):
        a0 = MARBLE + player * 0xe2
        _track_axis(m, a0, pa & 0xff, 0xc9, 0xc7)   # 0xF20001 -> +0xC7 (drives X velocity)
        _track_axis(m, a0, pb & 0xff, 0xc8, 0xc6)   # 0xF20003 -> +0xC6 (drives Y velocity)


# ---------------------------------------------------------------------------
# FUN_121B8 leaf routines
# ---------------------------------------------------------------------------
def swap_long_pair_12886(m, a2):
    vx, vy = m.rl(a2), m.rl(a2 + 4)
    m.wl(a2, vy); m.wl(a2 + 4, vx)


def vector_scale_25e7c(m, a2, mode):
    """Surface drag: scales vx/vy by a speed-dependent factor (ROM table 0x1EEF8)."""
    x, y = s32(m.rl(a2)), s32(m.rl(a2 + 4))
    ax, ay = abs(x) & 0xffffffff, abs(y) & 0xffffffff
    if ax > ay:
        d3 = (s16((ay >> 3) & 0xffff) * 3 + ax) & 0xffffffff
    else:
        d3 = (s16((ax >> 3) & 0xffff) * 3 + ay) & 0xffffffff
    i, f = (d3 >> 15) & 0xf, (d3 >> 12) & 7
    w5, w0 = s16(m.romw(0x1eef8 + 2 * i)), s16(m.romw(0x1eef8 + 2 * (i + 1)))
    d2 = s32((s32(s16(w0 - w5) * f) >> 3) + w5)
    if d3 < 0x100:
        d3 = 0x100
    second = None
    sub = lambda a, b: 0 if (a & 0xffffffff) >= b else (b - a) & 0xffffffff
    if mode == 2:
        d2 = s32(d2 << 2); d4 = sub(d2, d3)
    elif mode == 3:
        d4 = sub(d2, d3)
        d2 = s32(d2 + s32(d2 << 2)); second = sub(d2, d3)
    elif mode == 4:
        d4 = ((d2 >> 2) + d3) & 0xffffffff
    else:
        d4 = sub(d2, d3)
    div = (d3 >> 8) & 0xffff
    q = lambda v: ((((v << 6) & 0xffffffff) // div) & 0xffff) if div else 0
    # mode 3 (surfaces 0x2F-0x31, the grates): five times the drag across x, the
    # ordinary drag along y (ROM 0x25F80: the x factor comes from the second value)
    qx = q(d4) if second is None else q(second)
    qy = q(d4)
    m.wl(a2, s32(s16(x >> 8) * s16(qx)) >> 6)
    m.wl(a2 + 4, s32(s16(y >> 8) * s16(qy)) >> 6)


def slope_pull_25df6(m, a2):
    """Slope pull: velocity -= slope * 2048 (x4 on steep slopes); reversed on race 4."""
    for off in (0x4006a4, 0x4006a6):
        d = s16(m.rw(off))
        if abs(d) & 0xffff >= 12:
            m.ww(off, d << 2)
    px, py = s32(s16(m.rw(0x4006a4)) << 11), s32(s16(m.rw(0x4006a6)) << 11)
    if m.rw(0x400394) == 4:
        m.wl(a2, m.rl(a2) + px); m.wl(a2 + 4, m.rl(a2 + 4) + py)
    else:
        m.wl(a2, m.rl(a2) - px); m.wl(a2 + 4, m.rl(a2 + 4) - py)


def position_update_1706c(m, a2):
    """Push velocity along the edge directions flagged by FUN_1C676 (ROM table 0x23D40)."""
    d5, d6 = s32(m.rl(a2)), s32(m.rl(a2 + 4))
    i3 = s8(m.rb(0x40069f)); i2 = s8(7 - m.rb(0x4006a1)); i1 = s8(7 - m.rb(0x40069f)); i4 = s8(m.rb(0x4006a1))
    t = lambda i: s16(m.romw(0x23d40 + 2 * i))
    m4, m2, a4w, a0w = s16(t(i3)), s16(-t(i2)), s16(-t(i1)), s16(t(i4))
    g = lambda a: s16(m.rw(a)) > 0
    fpx, fpy, fnx, fny = m.rb(0x40066c), m.rb(0x40066e), m.rb(0x400670), m.rb(0x400672)
    if fpx and fpx < 3 and i3 < 4 and g(0x400674): d5 += m4
    if fpy and fpy < 3 and i2 < 4 and g(0x400676): d6 += m2
    if fnx and fnx < 3 and i1 < 4 and g(0x400678): d5 += a4w
    if fny and fny < 3 and i4 < 4 and g(0x40067a): d6 += a0w
    bm = m.rb(0x40066a)
    if bm & 1 and i3 < 4 and i2 < 4 and g(0x400674): d5 += m4; d6 += m2
    if bm & 2 and i2 < 4 and i1 < 4 and g(0x400676): d6 += m2; d5 += a4w
    if bm & 4 and i1 < 4 and i4 < 4 and g(0x400678): d5 += a4w; d6 += a0w
    if bm & 8 and i4 < 4 and i3 < 4 and g(0x40067a): d6 += a0w; d5 += m4
    m.wl(a2, d5); m.wl(a2 + 4, d6)


# ---------------------------------------------------------------------------
# FUN_1B5C2: walls and edges
# ---------------------------------------------------------------------------
def _abs_l(v): return abs(s32(v)) & 0xffffffff                     # 0x1B5A6
def _neg_if_pos(v): v = s32(v); return (-v if v > 0 else v) & 0xffffffff   # 0x1B5B4


def wall_bounce_1b5c2(m, a2, a3=0x40066a, d2=0x40069e):
    """Uses the edge heights/flags from FUN_1C676 to decide which way a wall faces.
    Velocity components pointing into a wall are reflected; if anything changed,
    the position is restored from the start of this update and 0x400666/0x400668 set."""
    d3 = d3_in = m.rl(a2)
    d4 = d4_in = m.rl(a2 + 4)
    d5 = s16(m.rw(0x40069a) - m.rw(0x400696))       # whole-tile movement this update, X
    d6 = s16(m.rw(0x40069c) - m.rw(0x400698))       # and Y
    wd2, wa0 = s16(m.rw(d2)), s16(m.rw(0x4006a0))   # position inside the cell (X, Y)
    gPX, gPY, gNX, gNY = (s16(m.rw(a)) for a in (0x400674, 0x400676, 0x400678, 0x40067a))
    g7C, g7E, g80, g82 = (s16(m.rw(a)) for a in (0x40067c, 0x40067e, 0x400680, 0x400682))
    fPX, fPY, fNX, fNY = (s8(m.rb(a)) for a in (0x40066c, 0x40066e, 0x400670, 0x400672))
    bm = m.rb(a3)
    # the four sides
    if (fPX and fPX < 3 and wd2 < 4 and gPX >= 4) or (fNX > 2 and d5 == 1 and gNX >= 4):
        d3 = _abs_l(d3)
    if (fPY and fPY < 3 and wa0 > 4 and gPY >= 4) or (fNY > 2 and d6 == -1 and gNY >= 4):
        d4 = _neg_if_pos(d4)
    if (fNX and fNX < 3 and wd2 > 4 and gNX >= 4) or (fPX > 2 and d5 == -1 and gPX >= 4):
        d3 = _neg_if_pos(d3)
    if (fNY and fNY < 3 and wa0 < 4 and gNY >= 4) or (fPY > 2 and d6 == 1 and gPY >= 4):
        d4 = _abs_l(d4)
    # the four corners
    if (bm & 0x01 and d5 != -1 and d6 != 1 and wd2 < 4 and wa0 > 4 and g7C >= 4) or \
       (bm & 0x40 and d5 != 0 and d6 != 0 and g80 >= 4):
        d4 = _neg_if_pos(d4); d3 = _abs_l(d3)
    if (bm & 0x02 and d5 != 1 and d6 != 1 and wd2 > 4 and wa0 > 4 and g7E >= 4) or \
       (bm & 0x80 and d5 != 0 and d6 != 0 and g82 >= 4):
        d4 = _neg_if_pos(d4); d3 = _neg_if_pos(d3)
    if (bm & 0x04 and d5 != 1 and d6 != -1 and wd2 > 4 and wa0 < 4 and g80 >= 4) or \
       (bm & 0x10 and d5 != 0 and d6 != 0 and g7C >= 4):
        d4 = _abs_l(d4); d3 = _neg_if_pos(d3)
    if (bm & 0x08 and d5 != -1 and d6 != -1 and wd2 < 4 and wa0 < 4 and g82 >= 4) or \
       (bm & 0x20 and d5 != 0 and d6 != 0 and g7E >= 4):
        d4 = _abs_l(d4); d3 = _abs_l(d3)
    if d3 != d3_in:
        m.wb(0x400666, 1)
        m.wl(a2 + 0x0c, m.rl(0x400684))          # back to the position at the start of the update
        m.wl(a2, d3)
    if d4 != d4_in:
        m.wb(0x400668, 1)
        m.wl(a2 + 0x10, m.rl(0x400688))
        m.wl(a2 + 4, d4)
    if m.rb(0x400666) or m.rb(0x400668):
        m.sound(0x34)                              # bounced off a wall


# ---------------------------------------------------------------------------
# FUN_121B8, opening section (up to the wall check) - assembled from ported parts
# ---------------------------------------------------------------------------
def physics_prefix_121b8(m, a2):
    """Returns False if the marble is embedded >16 px below the floor (the ROM's out-of-range exit)."""
    x, y, z = m.rl(a2 + 0x0c), m.rl(a2 + 0x10), m.rl(a2 + 0x14)
    m.wl(0x400684, x); m.wl(0x400688, y); m.wl(0x40068c, z)
    m.ww(0x40069a, asr(x, 19)); m.ww(0x40069c, asr(y, 19))
    m.ww(0x400698, 0xffff); m.ww(0x400696, 0xffff)
    sprite_pos_update_1bab2(m, a2)
    if s32(sprite_project_1cc62(m, 0) - m.rl(a2 + 0x14)) > 0x100000:
        return False
    for o in (0, 4, 8):                                   # position += velocity
        m.wl(a2 + 0x0c + o, m.rl(a2 + 0x0c + o) + m.rl(a2 + o))
    sprite_pos_update_1bab2(m, a2)
    bracket_lerp_1c676(m)
    d4, d5 = m.rl(a2), m.rl(a2 + 4)
    d1 = s16(m.rw(0x400692) - m.rw(0x400690) + 0x88)      # screen-bounds bounce
    if (d1 < 4 and s32(d4) > s32(d5)) or (d1 > 0x11c and s32(d5) > s32(d4)):
        swap_long_pair_12886(m, a2)
        mid = (s32(d5) + s32(d4)) >> 1
        m.wl(a2 + 0x0c, m.rl(0x400684) + mid)
        m.wl(a2 + 0x10, m.rl(0x400688) + mid)
        sprite_pos_update_1bab2(m, a2)
    m.wb(0x400668, 0); m.wb(0x400666, 0)
    wall_bounce_1b5c2(m, a2)
    return True


# ---------------------------------------------------------------------------
# Landing, state changes, respawn
# ---------------------------------------------------------------------------
def occupied_17cb8(m, me_ptr, x, y, radius):
    """FUN_17CB8: is anything (another marble, an enemy, or one of the four
    hazards at 0x401302) within `radius` of pixel (x, y)?  Returns 0, 1, 2 or 4."""
    def near(o):
        ox, oy = s16(m.rw(o + 0x0c)), s16(m.rw(o + 0x10))
        dx, dy = abs(s16(ox - x)) & 0xffff, abs(s16(oy - y)) & 0xffff
        if dx >> 3 > 3 or dy >> 3 > 3:
            return False
        a, b = (dx << 4) & 0xffff, (dy << 4) & 0xffff
        d = (b >> 3) * 3 + a if a > b else (a >> 3) * 3 + b
        return radius > d
    for i in range(s16(m.rw(0x400396))):
        o = 0x400018 + i * 0xe2
        if o != me_ptr and m.rb(o + 0x18) and near(o):
            m.wl(0x40046a, i); return 1
    for i, o in enumerate(ENEMY_SLOTS):
        if m.rb(o + 0x18) and near(o):
            m.wl(0x40046a, i); return 2
    for i in range(4):
        o = 0x401302 + i * 0x60
        if m.rb(o + 0x18) and near(o):
            m.wl(0x40046a, i); return 4
    return 0


def nearest_respawn_2637a(m, a2):
    """FUN_2637A: the free respawn point of the marble's zone (+0x1D) nearest its
    last safe tile (+0x32/+0x34).  Table per race at ROM 0x1EF1A: tile X, tile Y, zone, -."""
    a3 = m.read32(0x1ef1a + 4 * m.rw(0x400394))
    zone = s8(m.rb(a2 + 0x1d))
    best = 0x300
    while m.romb(a3) != 0xff:
        if m.romb(a3 + 2) == zone:
            tx, ty = m.romb(a3), m.romb(a3 + 1)
            a = (abs(s16(m.rw(a2 + 0x32) - tx)) << 4) & 0xffff
            b = (abs(s16(m.rw(a2 + 0x34) - ty)) << 4) & 0xffff
            d = (b >> 3) * 3 + a if a > b else (a >> 3) * 3 + b
            px, py = s16(tx * 8 + 4), s16(ty * 8 + 4)
            if not occupied_17cb8(m, a2, px, py, 0x180) and d < best:
                best = d
                m.wl(0x400462, px); m.wl(0x400466, py); m.wb(0x400472, m.romb(a3 + 2))
        a3 += 4


def pick_respawn_262b2(m, a2):
    """FUN_262B2: choose where a broken marble comes back (0x400462/466, zone 0x400472)."""
    m.wl(0x400466, 0); m.wl(0x400462, 0); m.wb(0x400472, 0xff)
    nearest_respawn_2637a(m, a2)
    if m.rb(0x400472) != 0xff:
        return
    a3 = m.read32(0x1ef1a + 4 * m.rw(0x400394))       # nothing near: the last free point of
    first = a3 - 4                                     # an earlier zone, searching backwards
    while m.romb(a3) != 0xff:
        a3 += 4
    zone = s8(m.rb(a2 + 0x1d))
    if zone:
        zone -= 1
    while a3 != first:
        tx, ty = m.romb(a3), m.romb(a3 + 1)
        px, py = s16(tx * 8 + 4), s16(ty * 8 + 4)
        if not occupied_17cb8(m, a2, px, py, 0x180) and m.romb(a3 + 2) == zone:
            m.wl(0x400462, px); m.wl(0x400466, py); m.wb(0x400472, m.romb(a3 + 2))
            return
        a3 -= 4


def respawn_2591a(m, a2):
    pick_respawn_262b2(m, a2)
    m.wl(a2 + 0x0c, m.rl(0x400462) << 16)          # respawn point, whole units -> 16.16
    m.wl(a2 + 0x10, m.rl(0x400466) << 16)
    m.ww(0x400698, 0xffff); m.ww(0x400696, 0xffff)
    sprite_pos_update_1bab2(m, a2)
    m.wl(a2 + 0x14, sprite_project_1cc62(m, 0))    # dropped onto the floor
    m.wb(a2 + 0x1b, m.rb(0x400472))
    for o in (0x08, 0x04, 0x00):
        m.wl(a2 + o, 0)
    m.wb(a2 + 0x56, 0); m.wb(a2 + 0x36, 0); m.wb(a2 + 0x58, 0)
    m.wl(a2 + 0x26, 0); m.wl(a2 + 0x22, 0)
    reset_orientation_25b40(m, a2)
    screen_pos_1b9cc(m, a2)
    zone_change_13966(m, a2)


def state_entry_25bae(m, a2, code, respawn=True):
    m.wl(a2 + 4, 0); m.wl(a2, 0)                     # stop
    if m.rb(a2 + 0x1a) == 6:
        m.wb(a2 + 0x18, 3)
    code &= 0xff
    if code == 2:                                    # dazed
        m.sound(0x38)
        m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x60, 2); m.wl(a2 + 0x5a, 0x20fde)
        m.wb(a2 + 0x56, 2); m.wb(a2 + 0x1a, 2)
    elif code == 9:
        m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x60, 4); m.wl(a2 + 0x5a, 0x21062); m.wb(a2 + 0x1a, 9)
    elif code == 4:                                  # broken (sound 0x3C or 0x3D)
        if respawn:
            respawn_2591a(m, a2)
        m.sound(0x3c if m.rb(a2 + 0x57) == 0x65 else 0x3d)      # broken
        m.wl(a2 + 0x5a, 0); m.wb(a2 + 0x1a, 4)
        m.ww(a2 + 0xd2, m.rw(a2 + 0xd2) + 1)         # times broken


def landing_25c74(m, a2, delta, respawn=True):
    mode = m.rb(a2 + 0x1a)
    d1 = s16(delta)
    player = a2 in (0x400018, 0x4000fa)
    if player:
        if m.rb(a2 + 0x1a) == 6:
            m.wb(a2 + 0x18, 3)
        m.wb(a2 + 0x1a, 1)
    else:
        m.wb(a2 + 0x1a, 0x24)
    imp = s16(s8(m.rb(a2 + 0x57)) + d1)              # impact meter
    m.wb(a2 + 0x57, 0x7f if imp > 0x7f else imp)
    if not player:
        st = s8(m.rb(a2 + 0x56) + (d1 & 0xff))
        m.wb(a2 + 0x56, st)
        if st <= 0x50 and d1 <= 0x1f:
            return
        remove_object_15bd0(m, a2, award=True, remove=False)   # crashed: points, crushed animation
        m.wb(a2 + 0x18, 2)
        state_entry_25bae(m, a2, 2, respawn)
        return
    b56, b57 = s8(m.rb(a2 + 0x56)), s8(m.rb(a2 + 0x57))
    if b56 + ((b57 - b56) >> 1) > 0x1f:              # hard landing
        safe = -0x10 <= s16(m.rw(a2 + 0x20)) < 0xf0
        if mode in (1, 5):
            stop_roll_15884(m)                         # (0x25D1A)
            m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x56, 1); m.wb(a2 + 0x60, 2); m.wl(a2 + 0x5a, 0x20fd2)
        if safe:
            state_entry_25bae(m, a2, 2, respawn)
        else:
            m.wb(a2 + 0x57, 0x64)
            state_entry_25bae(m, a2, 4, respawn)
        return
    if mode == 1:
        return
    m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x60, 2); m.wl(a2 + 0x5a, 0x20faa)   # soft-landing bounce
    m.sound(0x39)



# ---------------------------------------------------------------------------
# FUN_1BC88  marble vs. marble / enemy contact.  Walks the object table at ROM
# 0x24AC6 (P1, P2, and the two enemy slots 0x4009A4 / 0x400A20; races 1/3/5
# only use the first two).  On a touch: undo this update's move, swap the two
# objects' X/Y velocities and push them apart (table 0x24AD6, x4 between a
# marble and an enemy).  Returns 1 if anything was hit.
# ---------------------------------------------------------------------------
PLAYERS = (0x400018, 0x4000fa)


def _repel(m, vel_addr, mixed, neg):
    v = s16(m.rw(vel_addr))
    idx = s16(abs(v))                                  # move.b (a4,d0.w): word index
    d = s8(m.romb(0x24ad6 + idx)) << 12
    if mixed:
        d <<= 2
    return -d if neg else d


def contact_1bc88(m, a2):
    r = s16(m.rw(0x400394))
    count = 4 if r in (1, 3, 5) else 2
    hit = 0
    sx, sy, sz = s16(m.rw(0x400690)), s16(m.rw(0x400692)), s16(m.rw(0x400694))
    for i in range(count):
        a3 = (m.romb(0x24ac6 + 4 * i) << 24 | m.romb(0x24ac7 + 4 * i) << 16 |
              m.romb(0x24ac8 + 4 * i) << 8 | m.romb(0x24ac9 + 4 * i))
        if a3 == a2 or m.rb(a3 + 0x18) != 1:
            continue
        dx = s16(sx - m.rw(a3 + 0x0c))
        if not -7 <= dx <= 7: continue
        dy = s16(sy - m.rw(a3 + 0x10))
        if not -7 <= dy <= 7: continue
        dz = s16(sz - m.rw(a3 + 0x14))
        if not -14 <= dz <= 14: continue
        if m.rb(a3 + 0x1a) in (4, 2, 0x0b) or m.rb(a2 + 0x58) == 0x0a:
            continue
        ax, ay = (abs(dx) << 4) & 0xffff, (abs(dy) << 4) & 0xffff
        dist = (((ay >> 3) * 3 + ax) if ax >= ay else ((ax >> 3) * 3 + ay)) & 0xffff
        if dist >= 0x70:
            continue
        hit = 1
        p2, p3 = a2 in PLAYERS, a3 in PLAYERS
        m.wl(a2 + 0x0c, m.rl(0x400684)); m.wl(a2 + 0x10, m.rl(0x400688)); m.wl(a2 + 0x14, m.rl(0x40068c))
        for o in (0, 4):                               # swap vx, vy
            t = m.rl(a2 + o); m.wl(a2 + o, m.rl(a3 + o)); m.wl(a3 + o, t)
        mixed = p2 != p3
        gx = s32(m.rl(a2 + 0x0c)) > s32(m.rl(a3 + 0x0c))
        gy = s32(m.rl(a2 + 0x10)) > s32(m.rl(a3 + 0x10))
        d5 = _repel(m, a2, mixed, gx); d1 = _repel(m, a2 + 4, mixed, gy)
        m.wl(a2, m.rl(a2) - d5); m.wl(a2 + 4, m.rl(a2 + 4) - d1)
        d5 = _repel(m, a3, mixed, gx); d1 = _repel(m, a3 + 4, mixed, gy)
        m.wl(a3, m.rl(a3) + d5); m.wl(a3 + 4, m.rl(a3 + 4) + d1)
        for o, isp in ((a2, p2), (a3, p3)):            # enemies get knocked about
            if not isp:
                if m.rw(o + 0x6c) == 0xffff:
                    m.ww(o + 0x6c, 0x96)
                m.wb(o + 0x1a, 0x24); m.wb(o + 0x56, m.rb(o + 0x56) + 0x14)
                enter_regroup_160d4(m, o)
        m.sound(0x37 if p2 and p3 else 0x32)          # marble on marble / on an enemy
        if dz < 0 or m.rb(a2 + 0x36) == 0:
            continue
        if not p3:                                     # landed on top of an enemy: it's squashed
            remove_object_15bd0(m, a3, award=True, remove=False)
            m.wb(a3 + 0x18, 2)
        else:
            stop_roll_15884(m)
        state_entry_25bae(m, a3, 2)                    # landed on top of it: it's dazed
    return hit


# ---------------------------------------------------------------------------
# FUN_160F6  edges.  Uses the per-direction "drop" info from FUN_1C676
# (0x40066A-0x400682) to decide whether the marble has rolled over a ledge
# (+0x36 = 1, direction mask in +0x37), lifted off the floor by more than 6 px
# (+0x36 = 2, a real fall), or is back on the ground (+0x36 = 0).  While over a
# ledge (state 1) it is held up by the speed table at ROM 0x2398C until it has
# moved a full tile, then it drops.
# Note: the ROM keeps the SMALLEST table value among the candidate directions
# (cmpa.w d2,a1 / ble skip); marble-love keeps the largest.
# ---------------------------------------------------------------------------
_EDGE_CMDS = {0x00, 0x10, 0x12, 0x17, 0x18, 0x20, 0x2d, 0x2e, 0x2f, 0x30, 0x31,
              0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x3b}


def edges_160f6(m, a2, floor, _keep_max=False):
    d3 = s16(m.rw(0x40069a) - m.rw(0x400696))         # whole-tile movement, X
    d4 = s16(m.rw(0x40069c) - m.rw(0x400698))         # and Y
    tx, ty = s16(m.rw(0x40069e)), s16(m.rw(0x4006a0))  # position inside the cell
    g = lambda a: s16(m.rw(a))
    ok = lambda a: -1 < g(a) < 4
    d2 = 0
    f = m.rb(0x40066c)
    if f and f < 3 and tx < 4 and d3 != 1 and ok(0x400674): d2 |= 1
    f = m.rb(0x40066e)
    if f and f < 3 and ty > 4 and d4 != -1 and ok(0x400676): d2 |= 2
    f = m.rb(0x400670)
    if f and f < 3 and tx > 4 and d3 != -1 and ok(0x400678): d2 |= 4
    f = m.rb(0x400672)
    if f and f < 3 and ty < 4 and d4 != 1 and ok(0x40067a): d2 |= 8
    diag = m.rb(0x40066a)
    if diag & 1 and tx < 4 and ty > 4 and d3 == -1 and d4 == 1 and g(0x40067c) < 4: d2 |= 0x10
    if diag & 2 and tx > 4 and ty > 4 and d3 == 1 and d4 == 1 and g(0x40067e) < 4: d2 |= 0x20
    if diag & 4 and tx > 4 and ty < 4 and d3 == 1 and d4 == -1 and g(0x400680) < 4: d2 |= 0x40
    if diag & 8 and tx < 4 and ty < 4 and d3 == -1 and d4 == -1 and g(0x400682) < 4: d2 |= 0x80
    cmd = m.rb(a2 + 0x58)

    def start_fall(st):
        m.wb(a2 + 0x36, st)
        m.ww(a2 + 0x2e, m.rw(0x400696)); m.ww(a2 + 0x30, m.rw(0x400698))
        m.wl(a2 + 8, 0xffffa000)
        if st == 2 and m.rb(a2 + 0x58) not in (0x12, 0x20):
            m.sound(0x45)                              # falling (0x16432; not over a ledge)

    if d2:
        if cmd in _EDGE_CMDS:
            if m.rb(a2 + 0x36) == 0:
                m.wb(a2 + 0x37, 0)
            if s8(m.rb(a2 + 0x37)) != d2 and m.rb(a2 + 0x36) != 2:
                start_fall(1)
                m.wb(a2 + 0x37, d2)
    elif m.rb(a2 + 0x36) != 2:
        m.wb(a2 + 0x36, 0); m.wl(a2 + 8, 0)
        if s32(m.rl(a2 + 0x14) - floor) > 0x60000:     # more than 6 px above the floor
            start_fall(2)
    if m.rb(a2 + 0x36) != 1:
        return
    d5 = abs(s16(m.rw(0x400696) - m.rw(a2 + 0x2e)))   # tiles moved since going over
    d6 = abs(s16(m.rw(0x400698) - m.rw(a2 + 0x30)))
    if d5 >= 2 or d6 >= 2:
        return
    mask = s8(m.rb(a2 + 0x37))
    best, best_v = -1, 0
    sel = {1: (0x400674, lambda: (tx, 5) if d5 == 0 else (5, 5)),
           2: (0x400676, lambda: (7 - ty, 5) if d6 == 0 else (5, 5)),
           4: (0x400678, lambda: (7 - tx, 5) if d5 == 0 else (5, 5)),
           8: (0x40067a, lambda: (ty, 5) if d6 == 0 else (5, 5)),
           # the ROM compares abs() results against -1 here (never true); kept as is
           0x10: (0x40067c, lambda: (5, 5) if d5 == 0xffff or d6 == 1 else (tx, 7 - ty)),
           0x20: (0x40067e, lambda: (5, 5) if d5 == 1 or d6 == 1 else (7 - tx, 7 - ty)),
           0x40: (0x400680, lambda: (5, 5) if d5 == 1 or d6 == 0xffff else (7 - tx, ty)),
           0x80: (0x400682, lambda: (5, 5) if d5 == 0xffff or d6 == 0xffff else (tx, ty))}
    for bit in range(8):
        iso = mask & (1 << bit)
        if iso not in sel:
            continue
        va, fn = sel[iso]
        vel = g(va)
        a, b = fn()
        if a < 5:
            idx = a if (a > b or b >= 5) else b
        elif b < 5:
            idx = b
        else:
            continue
        mag = s8(m.romb(0x2398c + idx))
        if best == -1 or (mag > best if _keep_max else best > mag):   # ROM keeps the smallest
            best, best_v = mag, vel
    if best == -1:
        m.wb(a2 + 0x36, 2); m.wl(a2 + 8, 0xffffa000)
        if m.rb(a2 + 0x58) not in (0x12, 0x20):
            m.sound(0x45)                              # falling
    elif best_v - best > 0:
        m.wl(a2 + 0x14, m.rl(a2 + 0x14) + ((best_v - best) << 16))



# ---------------------------------------------------------------------------
# Zone lines and the goal: FUN_1365C (player only, from FUN_121B8)
# Per race, ROM 0x1EB16 points at 6-byte entries:
#   e0, e1, e2 (signed), zone A, zone B
#   e0 <  0: a row of tiles at Y = |e1|, X from |e0| to e2
#   e0 >= 0: a column of tiles at X = e0,  Y from |e1| to e2
# When the marble's tile changes and one of the two tiles is on the line and the
# other is not, its zone (+0x1B) becomes A or B depending on the side.  Zone 0xFF
# is the goal: state 6, with the race's roll-in velocity (ROM 0x1EB2E / 0x1EB34).
# The list ends with an entry whose zones are both 0xFF.
# ---------------------------------------------------------------------------
def zone_lines_1365c(m, a2):
    race = m.rw(0x400394)
    a0 = m.read32(0x1eb16 + 4 * race) - 6
    t_x, t_y = s16(m.rw(0x400696)), s16(m.rw(0x400698))     # (a1, a4) in the ROM
    u_x, u_y = s16(m.rw(0x40069a)), s16(m.rw(0x40069c))     # (-12, -10)
    if (t_x, t_y) == (u_x, u_y):
        return
    old_zone = m.rb(a2 + 0x1b)
    while True:
        a0 += 6
        d5, d6, d1 = s8(m.romb(a0)), s8(m.romb(a0 + 1)), s8(m.romb(a0 + 2))
        row = d5 < 0
        d6 = abs(d6)
        if row:
            d5 = abs(d5); start = d5
            near = d6 in (t_y, u_y)
        else:
            start = d6
            near = d5 in (t_x, u_x)
        if near:
            on_t = on_u = False
            for k in range(start, d1 + 1):
                xy = (k, d6) if row else (d5, k)
                if xy == (t_x, t_y): on_t = True
                if xy == (u_x, u_y): on_u = True
            if not on_t and on_u:
                if row:
                    zone = m.romb(a0 + 3) if t_y <= d6 else m.romb(a0 + 4)
                else:
                    zone = m.romb(a0 + 4) if t_x > d5 else m.romb(a0 + 3)
                m.wb(a2 + 0x1b, zone)
                if zone == 0xff:                                  # the goal line
                    if m.rb(0x4003a4) == 0xff and m.rb(a2 + 0x18) != 2:   # first over the line:
                        m.wb(0x4003a4, m.rb(a2 + 0x19))                 # 1000 + 500 per race
                        m.palette(m.rb(a2 + 0x19) + (2 if race > 0 else 0) + 4)   # the GOAL
                                                                         # outline in their colour
                        points_285b0(m, a2, 2 * race + 3)
                    m.wb(a2 + 0x6d, m.rb(a2 + 0x6e)); m.wb(a2 + 0x6e, 0xff)
                    stop_roll_15884(m)
                    m.wb(a2 + 0x1a, 6)
                    if race == 0 and old_zone in (3, 4):
                        m.wl(a2 + 4, 0); m.wl(a2, 0)
                    else:
                        m.wl(a2, s8(m.romb(0x1eb2e + race)) << 16)
                        m.wl(a2 + 4, s8(m.romb(0x1eb34 + race)) << 16)
                    m.wb(a2 + 0x57, 0x1e)                         # 30 updates rolling in
                    m.sound(m.read32(0x1ef72 + 4 * m.rb(a2 + 0x19)))   # 0x2F / 0x30: finished
                elif zone == 4 and race == 3:                     # Aerial: the vacuums shut down
                    for o in LEVEL_SLOTS:
                        if m.rb(o + 0x18) != 1:
                            continue
                        if m.rb(o + 0x1a) == 4:
                            level_object_init_12f44(m, o, 1, 0)
                        elif m.rb(o + 0x1a) == 2:
                            if m.rb(o + 0x1f) == 0x0b:
                                m.wl(o + 0x36, 0x1d752)
                            elif m.rb(o + 0x1f) == 0x0d:
                                m.wl(o + 0x36, 0x1d798)
                            if m.rb(o + 0x1f) in (0x0b, 0x0d):
                                m.sound(m.read32(0x1ef5a + 4 * (s8(m.rb(o + 0x1b)) - 30)))
                                level_object_program_12896(m, o)
                if m.rb(a2 + 0x1b) != old_zone:
                    zone_change_13966(m, a2)
                return
        if m.romb(a0 + 3) == 0xff and m.romb(a0 + 4) == 0xff:
            return


# ---------------------------------------------------------------------------
# Enemies (the steelies): two slots, 0x4009A4 and 0x400A20 (stride 0x7C).
#   +0x6E  current waypoint (ROM pointer into a path of 6-byte nodes:
#          tile X, tile Y, then 4 signed indexes of neighbouring nodes)
#   +0x72  the path's first node     +0x76/+0x78  scroll rows it lives in
#   +0x68  steering speed            +0x6C  "angry" timer (word; 0xFFFF at spawn)
#   +0x7A  index of the marble it is after
# States (+0x1A): 0x20 follow the path, 0x21 chase a marble, 0x22 roam towards it
#   by path, 0x23 return to the path, 0x24 knocked away after a bump.
# ---------------------------------------------------------------------------
ENEMY_SLOTS = (0x4009a4, 0x400a20)
OBJ_TABLE = 0x1eff6                    # P1, P2, enemy 0, enemy 1


def _octant(dx16, dy16):
    """The ROM's distance estimate: max + 3/8 min, on 16-bit unsigned words."""
    a, b = dx16 & 0xffff, dy16 & 0xffff
    return ((b >> 3) * 3 + a) & 0xffff if a > b else ((a >> 3) * 3 + b) & 0xffff


def _tile_dist(tx, ty, nx, ny):
    """Tile-space distance used by FUN_15C46 / FUN_15D10 (x16 then octant)."""
    return _octant((abs(s16(tx - nx)) << 4) & 0xffff, (abs(s16(ty - ny)) << 4) & 0xffff)


def nearest_node_15d10(m, a0):
    tx, ty = s16(s32(m.rl(a0 + 0x0c)) >> 19), s16(s32(m.rl(a0 + 0x10)) >> 19)
    a1 = m.rl(a0 + 0x72)
    best, best_ptr = 0x400, None
    while m.read8(a1) != 0xff and m.read8(a1 + 1) != 0xff:
        d = _tile_dist(tx, ty, s8(m.read8(a1)), s8(m.read8(a1 + 1)))
        if d < best:
            best, best_ptr = d, a1
        a1 += 6
    if best_ptr is None:                       # ROM writes a stale register here; keep the old node
        m.unported.add('FUN_15D10 no node within range')
        return
    m.wl(a0 + 0x6e, best_ptr)


def _node_link(m, a0, idx):
    k = s8(m.read8(m.rl(a0 + 0x6e) + 2 + idx))
    return (m.rl(a0 + 0x72) + (k & 0xffff) * 6) & 0xffffffff     # mulu #6 on the word


def best_link_15c46(m, a1):
    tgt = m.read32(OBJ_TABLE + 4 * s16(m.rw(a1 + 0x7a)))
    tx, ty = s16(s32(m.rl(tgt + 0x0c)) >> 19), s16(s32(m.rl(tgt + 0x10)) >> 19)
    best, best_i = 0x800, 0
    for i in range(4):
        n = _node_link(m, a1, i)
        d = _tile_dist(tx, ty, s8(m.read8(n)), s8(m.read8(n + 1)))
        if d < best:
            best, best_i = d, i
    return best_i


def follow_link_160ae(m, a0, idx):
    m.wl(a0 + 0x6e, _node_link(m, a0, idx))


def next_node_1605c(m, a2):
    st = m.rb(a2 + 0x1a)
    if st == 0x20:
        follow_link_160ae(m, a2, 0)
    elif st == 0x22:
        follow_link_160ae(m, a2, best_link_15c46(m, a2))


def enter_regroup_160d4(m, a2):
    m.wb(a2 + 0x1a, 0x23)
    nearest_node_15d10(m, a2)
    m.wl(a2 + 0x68, 0x70000)


def p2_ahead_15fe6(m, a1, a0):
    """1 if a1 (P2) is the better target than a0 (P1)."""
    if m.rb(a1 + 0x18) != 1 or m.rb(a0 + 0x18) != 1:
        return 0
    if m.rb(a0 + 0x1b) == m.rb(a1 + 0x1b):
        t = lambda o: s16((s32(m.rl(o + 0x10)) >> 19) + (s32(m.rl(o + 0x0c)) >> 19))
        return 1 if s16(t(a1)) > s16(t(a0)) else 0
    return 1 if s8(m.rb(a0 + 0x1b)) < s8(m.rb(a1 + 0x1b)) else 0


def choose_target_15e24(m, a2, at_node):
    old = m.rb(a2 + 0x1a)
    d2 = 1 if at_node else 0
    count = s16(m.rw(0x400396))
    left = m.rb(0x400397)
    a4 = None
    for i in range(count):
        o = 0x400018 + i * 0xe2
        if (m.rb(o + 0x18) == 1 and m.rb(o + 0x1b) == m.rb(a2 + 0x1b) and m.rb(o + 0x36) != 2
                and m.rb(o + 0x1a) in (0, 1, 5)):
            left = (left - 1) & 0xff
            a4 = o
    if s8(left) != count:                                  # somebody to go after
        if count == 2 and left == 0:
            a4 = 0x4000fa if p2_ahead_15fe6(m, 0x4000fa, 0x400018) else 0x400018
        m.ww(a2 + 0x7a, s8(m.rb(a4 + 0x19)))
        dx = (abs(s32(m.rl(a4 + 0x0c) - m.rl(a2 + 0x0c))) >> 12) & 0xffff
        dy = (abs(s32(m.rl(a4 + 0x10) - m.rl(a2 + 0x10))) >> 12) & 0xffff
        dist = (dy >> 3) * 3 + dx if dx > dy else (dx >> 3) * 3 + dy     # long, not masked
        if (dist < 0x280 and (m.rw(a2 + 0x6c) != 0 or m.rb(a2 + 0x1b) in (7, 9))
                and m.rb(a2 + 0x36) == 0):
            m.wb(a2 + 0x1a, 0x21)
        elif m.rw(a2 + 0x6c) != 0:
            m.wb(a2 + 0x1a, 0x22)
        elif m.rb(a2 + 0x1a) != 0x23:
            m.wb(a2 + 0x1a, 0x20)
    elif m.rb(a2 + 0x1a) != 0x23:
        m.wb(a2 + 0x1a, 0x20)
    if m.rb(a2 + 0x1a) != old:
        if old == 0x21:
            enter_regroup_160d4(m, a2)
        else:
            d2 = 1
    if d2:
        speed = {0x21: 0xc0, 0x22: 0x80, 0x20: 0x70, 0x23: 0x70}.get(m.rb(a2 + 0x1a), 0)
        m.wl(a2 + 0x68, speed << 12)
        next_node_1605c(m, a2)


def at_node_15db6(m, a0):
    n = m.rl(a0 + 0x6e)
    hit = (s8(m.read8(n)) == s32(m.rl(a0 + 0x0c)) >> 19 and
           s8(m.read8(n + 1)) == s32(m.rl(a0 + 0x10)) >> 19)
    if hit and m.rb(a0 + 0x1a) == 0x23:
        m.wb(a0 + 0x1a, 0x20)
    if m.rb(a0 + 0x1a) == 0x23:
        nearest_node_15d10(m, a0)
    else:
        choose_target_15e24(m, a0, hit)


def _divs(num, den):
    """68000 divs.w: 32/16 -> 16-bit quotient; on overflow the register is unchanged."""
    q = abs(num) // abs(den)
    if (num < 0) != (den < 0):
        q = -q
    return q if -0x8000 <= q <= 0x7fff else s16(num)


def steer_182ba(m, a2):
    at_node_15db6(m, a2)
    if m.rb(a2 + 0x36) != 2:
        a1 = m.rl(a2 + 0x6e)
        tgt = m.read32(OBJ_TABLE + 4 * s16(m.rw(a2 + 0x7a)))
        d1 = s8(m.rb(tgt + 0x1a))
        if (m.rb(a2 + 0x1a) == 0x21 and m.rb(tgt + 0x18) == 1 and m.rb(a2 + 0x1b) == m.rb(tgt + 0x1b)
                and d1 in (0, 1, 5)):
            d2 = s32(m.rl(tgt + 0x0c) - m.rl(a2 + 0x0c))          # straight at the marble
            d3 = s32(m.rl(tgt + 0x10) - m.rl(a2 + 0x10))
        else:                                                    # towards the middle of the node's tile
            d2 = s32((s8(m.read8(a1)) << 19) - m.rl(a2 + 0x0c) + 0x40000)
            d3 = s32((s8(m.read8(a1 + 1)) << 19) - m.rl(a2 + 0x10) + 0x40000)
        dist = _octant((abs(d2) >> 12) & 0xffff, (abs(d3) >> 12) & 0xffff)
        if dist:
            d2, d3 = s16(_divs(d2, s16(dist))), s16(_divs(d3, s16(dist)))
        else:
            d2 = d3 = 0
        if dist < 64:
            m.wl(a2 + 0x68, 0x10000)
        sp = s16(s32(m.rl(a2 + 0x68)) >> 8)
        d2 = (s32(sp * d2) >> 4)
        d3 = (s32(sp * d3) >> 4)
        d2 = s32(d2 - s32(m.rl(a2))) >> 5
        d3 = s32(d3 - s32(m.rl(a2 + 4))) >> 5
        if m.rb(a2 + 0x1a) == 0x24:                              # dazed after a bump: less grip
            k = 31 - s8(m.rb(a2 + 0x56))
            k = 0 if k < 0 else k
            d2 = s32(s16(d2 >> 8) * k) << 3
            d3 = s32(s16(d3 >> 8) * k) << 3
        m.wl(a2, m.rl(a2) + d2)
        m.wl(a2 + 4, m.rl(a2 + 4) + d3)
    elif m.rb(a2 + 0x36):                                         # falling: gravity
        vz = s32(m.rl(a2 + 8) + GRAVITY)
        m.wl(a2 + 8, max(vz, TERMINAL_VZ))
    flag_scaled_magnitude(m, a2)


def remove_object_15bd0(m, a0, award, remove):
    if remove:
        m.wb(a0 + 0x18, 0)
        m.unported.add('FUN_18F46')                 # sprite/score display bookkeeping
    if award:
        for i in range(s16(m.rw(0x400396))):
            o = 0x400018 + i * 0xe2
            if m.rb(o + 0x18) not in (0, 2):
                points_285b0(m, o, 3)                  # 1000 to every player still racing


def enemy_update_158f6(m, a2):
    if m.rb(a2 + 0x18) == 0:
        return
    t = s16(m.rw(a2 + 0x6c))
    if t > 0:
        m.ww(a2 + 0x6c, t - 1)
        if t - 1 == 0 and m.rb(a2 + 0x1a) in (0x21, 0x22):
            enter_regroup_160d4(m, a2)
    if m.rb(a2 + 0x1a) == 0x24:
        if m.rb(a2 + 0x56):
            m.wb(a2 + 0x56, m.rb(a2 + 0x56) - 1)
        if m.rb(a2 + 0x56) == 0:
            enter_regroup_160d4(m, a2)
    elif m.rb(a2 + 0x18) == 2:                      # crushed: animation scripts, not ported
        m.unported.update({'FUN_25FC2', 'FUN_1B9CC', 'FUN_1281C'})
        return
    helper_253bc(m, a2)
    steer_182ba(m, a2)
    physics_121b8(m, a2)


def enemies_update_158cc(m):
    for a2 in ENEMY_SLOTS:
        enemy_update_158f6(m, a2)


# --- spawning (FUN_15A12 via FUN_144E4, as the screen scrolls) -------------------
def free_slot_1599a(m):
    slot = None
    for i in range(2):
        o = m.read32(0x1effe + 4 * i)
        if m.rb(o + 0x18) == 0:
            slot = o
    return slot


def spawned_already_159d8(m, entry):
    path = m.read32(entry + 2)
    return any(m.rb(o + 0x18) and m.rl(o + 0x72) == path for o in ENEMY_SLOTS)


def spawn_enemies_15a12(m, old_row, new_row):
    a3 = m.read32(0x22706 + 4 * m.rw(0x400394))
    while m.romb(a3) != 0xff:
        a2 = free_slot_1599a(m)
        if a2 is None:
            break
        lo, hi = s8(m.romb(a3)), s8(m.romb(a3 + 1))
        if not spawned_already_159d8(m, a3) and new_row in (lo, hi) and (old_row < lo or old_row > hi):
            path = m.read32(a3 + 2)
            m.wl(a2 + 0x6e, path); m.wl(a2 + 0x72, path)
            m.wb(a2 + 0x1b, m.romb(a3 + 6))
            m.ww(a2 + 0x76, lo); m.ww(a2 + 0x78, hi - (16 if path == 0x2276e else 0))
            m.wl(a2 + 0x0c, ((s8(m.read8(path)) << 19) + 0x40000) & 0xffffffff)
            m.wl(a2 + 0x10, ((s8(m.read8(path + 1)) << 19) + 0x40000) & 0xffffffff)
            m.ww(0x400698, 0xffff); m.ww(0x400696, 0xffff)
            sprite_pos_update_1bab2(m, a2)
            m.wl(a2 + 0x14, sprite_project_1cc62(m, 0))
            m.ww(a2 + 0x6c, 0xffff)
            m.wl(a2 + 4, 0); m.wl(a2, 0)
            m.wb(a2 + 0x18, 1); m.wb(a2 + 0x36, 0); m.wb(a2 + 0x1a, 0x20)
            m.wl(a2 + 0x22, 0); m.wl(a2 + 0x1e, 0)
            m.wb(a2 + 0x67, 0); m.wb(a2 + 0x56, 0); m.wb(a2 + 0x58, 0)
            for k in range(5):
                m.ww(a2 + 0x38 + 6 * k, 0)
            screen_pos_1b9cc(m, a2)
            m.unported.add('FUN_18E6C')
        a3 += 8
    for a2 in ENEMY_SLOTS:                                    # scrolled past: gone (no points)
        if m.rb(a2 + 0x18) == 0:
            continue
        lo, hi = s16(m.rw(a2 + 0x76)), s16(m.rw(a2 + 0x78))
        if (old_row == lo and new_row < lo) or (old_row == hi and new_row > hi):
            remove_object_15bd0(m, a2, award=False, remove=True)


def race_objects_start(m, players=1):
    """The parts of the race start (FUN_10508) that set up objects: the player
    count, the object index bytes, and the first scroll, which spawns any enemy
    whose rows start at the top of the course (ROM 0x105C4..0x105FE)."""
    m.ww(0x400396, players)
    m.wb(0x4003a4, 0xff)                               # nobody over the goal line yet (ROM 0x10FA4)
    # the flags FUN_10504 sets (ROM 0x10530..0x105B2; the rest it clears are 0 here)
    m.wb(0x40075c, 0)                                  # no moving floors yet
    m.wl(0x400408, 0x40040c)
    m.wb(0x40075e, 1)                                  # Intermediate's waves may start
    m.wb(0x400460, 0xff)
    m.ww(0x400768, 0xffff)
    for i, o in enumerate((0x400018, 0x4000fa)):
        m.wb(o + 0x19, i)
    # every object table's slot numbers (ROM 0x10392..0x1044E): +0x19 = own index
    for base, n, stride in ((0x4019f8, 10, 56), (0x401890, 9, 40), (0x401482, 7, 0x42),
                            (0x401302, 4, 0x60), (0x4009a4, 2, 124), (0x400a9c, 25, 0x56)):
        for i in range(n):
            m.wb(base + stride * i + 0x19, i)
    for i, o in enumerate(ENEMY_SLOTS):
        m.wb(o + 0x19, i)
    scroll = s32(m.rl(0x40097c))                        # as FUN_16EC6 set it (on Silly the
                                                        # header's +18 below the top: ROM 0x105CA
                                                        # only reads it)
    step = 25 if m.rw(0x400394) == 4 else -25           # Silly Race scrolls the other way
    scroll_rows_144e4(m, scroll + step, scroll)


def scroll_13ee6(m):
    """FUN_13EE6, the screen scroll (first thing in the 30 Hz tick).

    Once every 8 ticks (tick count 0x400010), or every tick while a scroll is under
    way (0x400008), the players' screen heights (+0x20, sprite Y counts up from the
    bottom) are compared with two lines: a marble below Y 72 scrolls the screen on
    down the course, one above Y 168 scrolls it back (Silly Race: the other way
    round).  The speed is 1, 2 or 4 lines a tick, picked by how far past the line
    the marble is.  A scroll that starts runs on until the marble is 16 lines back
    inside the window, and stops at either end of the course.  When it stops
    because no marble qualifies, the scroll still eases on for a few lines (+0x0C).

    The playfield redraw (new tile rows into playfield RAM, 0x400006) and FUN_1344C
    are done elsewhere in this port: the level picture is drawn whole."""
    playfield_animation_1344c(m)               # last tick's animation frame (ROM 0x13F00)
    hdr = m.rl(0x400474)
    m.wb(0x400006, 0)                          # the row redraw it asks for (not needed here)
    if m.rl(0x400010) & 7 and not m.rb(0x400008):
        return
    race4 = m.rw(0x400394) == 4
    limit = 0x9000 if race4 else 0x7000
    d3 = s16(limit)
    a = 0x400018
    for _ in range(s16(m.rw(0x400396))):
        d4 = s16(m.rw(a + 0x20))
        mode = s8(m.rb(a + 0x1a))
        if m.rb(a + 0x18) == 1 and not (m.rb(a + 0x36) == 2 and s32(m.rl(a + 8)) <= 0) \
                and mode in (0, 1, 4, 5, 3):
            if not race4 and d4 < d3:
                d3 = d4
            elif race4 and d4 > d3:
                d3 = d4
        a += 0xe2
    scrolling = m.rb(0x400008)
    if d3 == s16(limit) and not scrolling:
        return
    lo, hi = s16(m.romw(0x1f1d0)), s16(m.romw(0x1f1d2))          # 72, 168
    near, far = s16(m.romw(0x1f1d4)), s16(m.romw(0x1f1d6))       # 16, 48
    v1, v2, v4 = s8(m.romb(0x1f1ca)), s8(m.romb(0x1f1cc)), s8(m.romb(0x1f1ce))
    dirn = s8(m.rb(0x400004))
    if d3 == s16(limit):                       # nobody qualifies: ease on (+0x0C)
        if m.rw(0x40000c) == 0:
            spd = s8(m.rb(0x40000a))
            if spd == v4:
                ease = lo - far if dirn == 1 else far + hi
            elif spd == v2:
                ease = lo - near if dirn == 1 else near + hi
            else:
                ease = lo if dirn == 1 else hi
            m.ww(0x40000c, ease & 0xffff)
        d3 = s16(m.rw(0x40000c))
    else:
        m.ww(0x40000c, 0)
    d4 = 0
    d5 = s16(m.rw(0x400000))
    d6 = s16(m.rw(0x40097e))
    fwd = (d3 < lo and d3 != s16(limit)) or (m.rb(0x400008) and dirn == 1)
    if fwd:
        if d3 < lo - far:
            m.wb(0x40000a, v4 & 0xff)
        elif d3 < lo - near:
            m.wb(0x40000a, v2 & 0xff)
        elif d3 > lo:
            m.wb(0x40000a, v1 & 0xff)
        spd = s8(m.rb(0x40000a))
        d5 = s16(d5 + spd)
        if d5 > 0x1ff:
            d5 -= 0x200
        d6 = s16(d6 + spd)
        d4 = 1
        if d6 > s16(m.read16(hdr + 18)) + s16(m.read16(hdr + 16)):   # bottom of the course
            m.wb(0x400008, 0)
            d6, d5 = s16(m.rw(0x40097e)), s16(m.rw(0x400000))
        elif (s16(m.rw(0x400000)) >> 3) != (d5 >> 3):
            m.wb(0x400006, 1)
    else:
        bwd = (d3 > hi and d3 != s16(limit)) or (m.rb(0x400008) and dirn == -1)
        if bwd:
            if d3 > far + hi:
                m.wb(0x40000a, v4 & 0xff)
            elif d3 > near + hi:
                m.wb(0x40000a, v2 & 0xff)
            elif d3 < hi:
                m.wb(0x40000a, v1 & 0xff)
            spd = s8(m.rb(0x40000a))
            d5 = s16(d5 - spd)
            if d5 < 0:
                d5 += 0x200
            d6 = s16(d6 - spd)
            d4 = -1
            if d6 < s16(m.read16(hdr + 16)):                         # top of the course
                m.wb(0x400008, 0)
                d6, d5 = s16(m.rw(0x40097e)), s16(m.rw(0x400000))
            elif (s16(m.rw(0x400000)) >> 3) != (d5 >> 3):
                m.wb(0x400006, 1)
    old = s32(m.rl(0x40097c))
    if d6 != old:
        scroll_rows_144e4(m, old, d6)
        m.ww(0x400000, d5 & 0xffff)
        m.wl(0x40097c, d6 & 0xffffffff)
        if m.rw(0x40000c):
            spd = s8(m.rb(0x40000a))
            m.ww(0x40000c, (m.rw(0x40000c) + (spd if s8(m.rb(0x400004)) == 1 else -spd)) & 0xffff)
        m.wb(0x400008, 1)
        m.wb(0x400004, d4 & 0xff)
    if d3 == s16(limit):
        return
    dirn = s8(m.rb(0x400004))
    if (d3 > lo + 16 and dirn == 1) or (d3 < hi - 16 and dirn == -1):
        m.wb(0x400008, 0)


def scroll_rows_144e4(m, old_scroll, new_scroll):
    base = s16(m.read16(m.rl(0x400474) + 16))
    o = s8((s32(old_scroll) - base) >> 4)
    n = s8((s32(new_scroll) - base) >> 4)
    if o != n:
        spawn_enemies_15a12(m, o, n)
        hazards_spawn_14c46(m, o, n)                             # hopping hazards
        slinkies_spawn_17346(m, o, n)                            # the 0x401482 objects
        level_objects_spawn_12dfa(m, o, n)
        if m.rw(0x400394) == 3:                                  # Aerial (0x14586)
            if o < 0x29 <= n:
                m.palette(9)
            if n < 0x29 <= o:
                m.palette(8)
        if m.rw(0x400394) == 4:                                  # the Silly Race (0x145B6)
            inside = lambda r, lo, hi: lo <= r <= hi
            if not inside(o, 0x1d, 0x38) and inside(n, 0x1d, 0x38):
                pit_start_18ffa(m)
            if inside(o, 0x1d, 0x38) and not inside(n, 0x1d, 0x38):
                pit_end_190ee(m)
            if not inside(o, 3, 0x1b) and inside(n, 3, 0x1b):
                m.wb(0x400762, 1)
            if inside(o, 3, 0x1b) and not inside(n, 3, 0x1b):
                m.wb(0x400762, 0)




def screen_pos_1b9cc(m, a2, refresh=False):
    """FUN_1B9CC: the object's screen position, X = y - x + 0x88 and
    Y = scroll + z + 0x54 - (x + y)/2, kept with the two previous ones at +0x1E,
    +0x22, +0x26 (a position equal to one of the last two is not taken: no jitter)."""
    if refresh:
        m.ww(0x400698, 0xffff); m.ww(0x400696, 0xffff)
    sprite_pos_update_1bab2(m, a2)
    z = s16(m.rw(a2 + 0x14))
    if m.rb(a2 + 0x58) in (0x2f, 0x30, 0x31) and m.rb(0x400691) & 1 and abs(s32(m.rl(a2))) > 0x4000:
        z = s16(z + 2)
    x, y = s16(m.rw(0x400690)), s16(m.rw(0x400692))
    sx = s16(y - x + 0x88)
    sy = s16(s16(m.rw(0x40097e)) + z + 0x54 - ((x + y) >> 1))
    v = ((sx & 0xffff) << 16) | (sy & 0xffff)
    if v in (m.rl(a2 + 0x1e), m.rl(a2 + 0x22)):
        return
    m.wl(a2 + 0x26, m.rl(a2 + 0x22)); m.wl(a2 + 0x22, m.rl(a2 + 0x1e)); m.wl(a2 + 0x1e, v)


# ---------------------------------------------------------------------------
# Hiding behind walls: FUN_177F8 + FUN_264AA (marble mode)
# ---------------------------------------------------------------------------
def terrain_top_177f8(m, row, col, which):
    """FUN_177F8: height of a terrain cell's top edge (the half picked by `which`
    and the parity flag at 0x400988), in the same units as the row base heights."""
    hdr = m.rl(0x400474)
    row = s16(row)
    if row >= s16(m.read16(hdr + 24)):
        return 0
    d3 = s32(s16(which) + s32(m.rl(0x400988)))
    d0 = s16(((s16(col) + s16(m.rw(0x40098a))) & 0xffff)) >> 1
    d0 = s16(d0 + 2)
    rp = (row & 0xff) & ~1
    if row & 1:
        d0 = s16(d0 + 0x16)
    a1 = (0xa00000 + s16(m.romw(0x1eb3a + rp))) & 0xffffffff
    a1 = (a1 + m.romb(0x1ed0a + d0) - 2) & 0xffffffff
    idx = (m.read32(a1) >> (m.romb(0x1ed0a + 0x2c + d0) & 0x1f)) & 0x7fe
    code = m.read16((m.rl(0x40065a) + idx) & 0xffffffff)
    base = s16(m.rw(0x400478 + 2 * row))
    while True:
        if code & 0xf000:
            if (code & 0xf000) & m.romw(0x24176 + 2 * d3):
                return s16((code & 0x7f) - 0x40 + base)
            c = m.romw(0x1ed62 + ((code & 0xf80) >> 6))
            if c == 0x1000:
                return 0
            return s16((code & 0x7f) - 0x40 - s16(c) + base)
        if not code & 0xfff:
            return 0
        if code & 0x800:
            code = m.rw(0x40076e + (code & 0x7fe))
            continue
        rec = (m.read32(hdr) + s16(code)) & 0xffffffff
        a2 = 0x2417e + 8 * d3
        b0 = m.read8((rec + m.read32(a2)) & 0xffffffff)
        b1 = m.read8((rec + m.read32(a2 + 4)) & 0xffffffff)
        d = max(b0, b1)
        if d == 0:
            return 0
        return s16(d - 0x80 + base)


def cover_264aa(m, a2, who=0):
    """FUN_264AA, who = 0 (a marble): for each 8-pixel screen column the marble
    covers, find whether terrain in front of it rises over it, and list up to 5
    mask sprites at +0x38 (code, x, y).  Code bits 11-13 = height in tiles - 1, bits
    0-10 = first tile, bit 15 = mirrored; drawn with the sprite priority bit, which
    puts the playfield over the marble inside the mask.  Clears +0x1C (don't draw
    the marble at all) when every column is covered.
    who = 2 (a hopping hazard at 0x401302): the box comes from its current animation
    frame (+0x58), its screen position from +0x28, the list goes to +0x2C, and the
    hand-placed table and the visibility flag are not used."""
    base = a2 + {2: 0x2c, 3: 0x26}.get(who, 0x38)
    cur = [base]
    for k in range(3 if who == 3 else 5):
        m.ww(base + 6 * k, 0)
    m.cover_cols = []                    # debug only: (column, how it was decided, result)

    def emit(code, x, y):
        m.ww(cur[0], code); m.ww(cur[0] + 2, x); m.ww(cur[0] + 4, y); cur[0] += 6

    x, y = s32(m.rl(a2 + 0x0c)), s32(m.rl(a2 + 0x10))
    if who == 3:                                   # the Silly Race's dropped ones (FUN_19E42)
        d4, d5 = 3, 16
        d6 = s16(s16(m.rw(a2 + 0x20)) + 3)
        sy = s16(s16(m.rw(a2 + 0x22)) - 8)
        d5 = s16(d5 + sy)
    elif who == 2:
        fr = m.read32(m.rl(a2 + 0x58))
        d4 = 5 if s8(m.romb(fr + 2)) > 2 else 3
        d5 = s16(s8(m.romb(fr + 3)) << 3)
        d6 = s16(s8(m.romb(fr)) + s16(m.rw(a2 + 0x28)))
        sy = s16(s8(m.romb(fr + 1)) + s16(m.rw(a2 + 0x2a)))
        d5 = s16(d5 + sy)
    else:
        if who == 0:
            if (m.rb(a2 + 0x58) and m.rb(a2 + 0x59) != 0xff and m.rb(a2 + 0x58) != 0x0a
                    and m.rb(a2 + 0x1a) not in (9, 0x0b, 0x0a)):
                m.wb(a2 + 0x1c, 0)
                return
            if m.rb(a2 + 0x1a) in (4, 6, 10):
                return
        sy = s16(m.rw(a2 + 0x20)); d6 = s16(m.rw(a2 + 0x1e))
        d4, d5 = 3, 14
        if who == 0 and m.rb(a2 + 0x1a) in (1, 2, 5, 9):    # (an enemy, who = 1: always 3 / 14)
            d4, d5 = 5, 32
        d5 = s16(d5 + sy)
    rowsum = s16((y >> 19) + (x >> 19) - 0x15)                    # -22
    rowdiag = s16(((x + y) >> 19) - 0x15)                         # -24
    d6 = d6 >> 3
    if who == 2:
        d6 = s16(d6 + (1 if d4 == 3 else 2))
    tbl = 0x1eaba
    if rowsum != rowdiag:
        tbl += 10
    elif (d6 ^ rowsum) & 1:
        tbl += 5
    if d4 == 3:
        tbl += 1; a4 = s16(d6 - 1)
    else:
        a4 = s16(d6 - 2)
    if a4 < 0:
        d4 = s16(d4 + a4); tbl -= a4; a4 = 0
    elif d4 + a4 > 36:
        d4 = 36 - a4
    hdr = m.rl(0x400474)
    colptr = (s16(m.rw(0x40045c)) + hdr + 46 + a4 * 4 - 4) & 0xffffffff
    covered = 0
    end = d4 + a4
    scroll = s16(m.rw(0x40097e) - m.read16(hdr + 16))
    race = m.rw(0x400394)
    zint = s32(m.rl(a2 + 0x14)) >> 16
    col = a4
    while col < end:
        row = s16(s8(m.romb(tbl)) + rowsum); tbl += 1
        colptr += 4
        if row < 0:
            row = 0
        action = None                                             # 'emit', 'full', 'next' or None
        a1 = m.read32(0x1ea2a + 4 * col)                          # per-column special cases
        off = s16(m.read16(a1 + 2 * race)) if who < 2 else 0
        if off:
            a1 = (a1 + 2 * off) & 0xffffffff
            while True:
                lim = s16(m.read16(a1))
                if lim == -1:
                    break
                if lim < row:
                    a1 += 10; continue
                if row < lim - s16(m.read16(a1 + 2)):
                    break
                kind = s16(m.read16(a1 + 4))
                yy = s16(s16(m.read16(a1 + 6)) + scroll)
                code = m.read16(a1 + 8)
                if 0 <= kind < 8:
                    action = 'full' if yy >= sy - kind else ('emit', code, yy)
                elif kind == 8:
                    action = ('emit', code, yy) if yy < d5 else 'next'
                elif kind > 0:
                    if yy <= d5:
                        action = ('emit', code, yy)
                    else:
                        row = kind & 0xff
                elif kind == -2:
                    action = 'next' if s16(code) <= zint else 'full'
                else:
                    action = 'next' if (row == lim and rowdiag == lim) else ('emit', code, yy)
                break
        source = 'table' if action is not None else 'terrain'
        if action is None:                                        # the column's occluder bitmap
            bit = row & 7
            p0 = ((row >> 3) + m.read32(colptr)) & 0xffffffff
            p = p0
            row &= ~7
            d2 = m.romb(0x1eaca + bit) & m.read8(p); p += 1
            if d2 == 0:
                while m.read8(p) == 0:
                    p += 1
                d2 = m.read8(p)
                row = s16(row + (p - p0) * 8)
            b = 1
            while not d2 & b:
                b <<= 1; row = s16(row + 1)
            m.wl(0x400988, (col ^ row) & 1)
            ref = s16(row * 4 - s16(m.rw(0x40097e)))
            top0 = s16(terrain_top_177f8(m, row, col, 0) - ref)
            if top0 > d5 + 16:
                action = 'full'
            elif top0 < sy - 16:
                action = 'next'
            else:
                m.wl(0x400988, m.rl(0x400988) ^ 1)
                ref = s16(ref + 4)
                top1 = s16(terrain_top_177f8(m, row + 1, col, 2) - ref)
                if not (top1 > sy) and not (top0 > sy):
                    action = 'next'
                elif top1 < d5 or top0 < d5:
                    if m.rl(0x400988) == 0:
                        top0, top1 = top1, top0
                    if top0 <= top1:
                        k = top1 - top0
                        action = ('emit', m.romw(0x1ead2 + 2 * k), s16(s16(m.romw(0x1eaf4 + 2 * k)) + top0))
                    else:
                        k = top0 - top1
                        action = ('emit', (m.romw(0x1ead2 + 2 * k) + 0x8000) & 0xffff,
                                  s16(s16(m.romw(0x1eaf4 + 2 * k)) + top1))
                else:
                    action = 'full'
        m.cover_cols.append((col, source, 'full' if action == 'full' else
                             ('mask' if isinstance(action, tuple) else 'clear')))
        if action == 'full':
            covered |= 1 << ((col - a4) & 0xff)
            emit(0x6ce0, col * 8, s16(d5 - 0x2f))
        elif isinstance(action, tuple):
            emit(action[1], col * 8, action[2])
        col += 1
    if who >= 2:
        return
    if (s16(covered & 0xffff) & 0xffffffff) == (((1 << (d4 & 63)) - 1) & 0xffffffff):   # asl.l d4: count mod 64
        m.wb(a2 + 0x1c, 0)
    elif who == 0 and d4 == 5 and covered & 14 == 14:
        m.wb(a2 + 0x1c, 0)


def sprite_list_1281c(m, a2):
    """FUN_1281C: visible if the screen Y is on screen; then the cover masks."""
    m.wb(a2 + 0x1c, 0)
    y = s16(m.rw(a2 + 0x20))
    if -16 < y < 0x100:
        m.wb(a2 + 0x1c, 1)
        cover_264aa(m, a2, 0 if a2 in (0x400018, 0x4000fa) else 1)

# ---------------------------------------------------------------------------
# FUN_1C014: the marble's rolling stripe.
# The marble keeps a 3-D orientation: two unit vectors (x at +0x74, y at +0x84,
# z at +0x94, 14-bit fixed point) on its "equator", from which 8 points are made.
# Each update they are rotated about the horizontal axis perpendicular to the
# velocity by an angle proportional to the speed (cosine table 0x1EDA2), and one
# vector per update is renormalised (+0xCA counts 0..7).  The 8 points are then
# projected to the screen, sorted left to right, and paired into 4 short line
# segments, each drawn as one 8x8 sprite picked by its slope (table 0x24ADE,
# +0x8000 = mirrored).  The 4 segments (code, x, y) are stored at +0xA4.
# ---------------------------------------------------------------------------
def _w(v): return s16(v)


def _interp_1c61e(m, v):
    """FUN_1C61E: interpolated lookup in table 0x1EE6E (1/sqrt-like), index (v>>10)-12."""
    v &= 0xffff
    i = s16((v >> 10) - 12)
    f = v & 0x3ff
    t0 = s16(m.romw(0x1ee6e + 2 * i)); t1 = s16(m.romw(0x1ee6e + 2 * (i + 1)))
    return s16((s32(f * s16(t1 - t0)) >> 10) + t0)


def _cos_1eda2(m, a):
    i, f = s16(a) >> 6, a & 0x3f
    t0 = s16(m.romw(0x1eda2 + 2 * i)); t1 = s16(m.romw(0x1eda2 + 2 * (i + 1)))
    return s16(s16((s32(s16(t1 - t0) * f)) >> 6) + t0)


def _mul14(a, b): return s16(s32(s16(a) * s16(b)) >> 14)


def stripe_1c014(m, a2):
    race = m.rw(0x400394)
    if m.rb(a2 + 0x58) == 0x0a:
        a4 = vy = 0
    else:
        a4 = s16(s32(m.rl(a2)) >> 4); vy = s16(s32(m.rl(a2 + 4)) >> 4)
    d2, d6 = abs(a4) & 0xffff, abs(vy) & 0xffff
    rotate = True
    if d2 and d6:
        general = True
    elif not d2 and not d6:
        if race == 4:                                   # Silly race: always turning
            vy = d6 = d2 = a4 = 16
            general = True
        else:
            general = rotate = False
    else:
        general = False
        if d2:
            d4, d3, d5 = 0x4000, d2, 0
        else:
            d4, d3, d5 = 0, d6, 0x4000
        if a4 >= 0: d4 = s16(-d4)
        if vy < 0: d5 = s16(-d5)
    if rotate:
        if general:
            t = ((d6 << 14) // d2 if d2 > d6 else (d2 << 14) // d6) & 0xffff
            d3 = (((t * t) >> 14) + 0x4000) & 0xffff
            d3 = _interp_1c61e(m, d3) & 0xffff
            if d2 > d6:
                d4 = (0x10000000 // d3) & 0xffff; d5 = ((t * d4) >> 14) & 0xffff
            else:
                d5 = (0x10000000 // d3) & 0xffff; d4 = ((t * d5) >> 14) & 0xffff
            d4, d5 = s16(d4), s16(d5)
            if a4 >= 0: d4 = s16(-d4)
            if vy < 0: d5 = s16(-d5)
            d3 = (((d2 if d2 > d6 else d6) * d3) >> 14) & 0xffff
        ang = (d3 & 0xffff) // 7
        if race == 4:
            ang = s16(0x600 - ang)
            if ang < 0: ang = 0
        c = _cos_1eda2(m, ang)                          # cos(angle)
        sn = _cos_1eda2(m, s16(0x1922 - ang))           # sin(angle)
        xx, yy = _mul14(d4, d4), _mul14(d5, d5)
        r00 = s16(_mul14(c, xx) + yy)
        r01 = _mul14(d4, _mul14(d5, s16(0x4000 - c)))
        r02 = s16(s32(-(s32(sn * d4))) >> 14)
        r11 = s16(_mul14(c, yy) + xx)
        r12 = _mul14(sn, d5)
        r20, r21, r22 = s16(-r02), s16(-r12), c
        for k in range(2):
            x = s16(m.rw(a2 + 0x74 + 2 * k)); y = s16(m.rw(a2 + 0x84 + 2 * k)); z = s16(m.rw(a2 + 0x94 + 2 * k))
            nx = s16(s32(r02 * z + r01 * y + r00 * x) >> 14)
            ny = s16(s32(r12 * z + r11 * y + r01 * x) >> 14)
            nz = s16(s32(r22 * z + r21 * y + r20 * x) >> 14)
            if m.rb(a2 + 0xca) == k:                     # keep it unit length
                n2 = (nz * nz + ny * ny + nx * nx) & 0xffffffff
                if n2 < 0x1f80000 or n2 > 0x2080000:
                    q = (0x2000000 // ((n2 >> 14) & 0xffff)) & 0xffff
                    f = _interp_1c61e(m, q)
                    nx, ny, nz = _mul14(f, nx), _mul14(f, ny), _mul14(f, nz)
            m.ww(a2 + 0x74 + 2 * k, nx); m.ww(a2 + 0x84 + 2 * k, ny); m.ww(a2 + 0x94 + 2 * k, nz)
        for base in (0x74, 0x84, 0x94):                   # the other 6 points
            p0, p1 = s16(m.rw(a2 + base)), s16(m.rw(a2 + base + 2))
            for j, v in enumerate((-p0, -p1, p1 + p0, -p0 - p1, p0 - p1, -p0 + p1)):
                m.ww(a2 + base + 4 + 2 * j, v)
        m.wb(a2 + 0xca, (m.rb(a2 + 0xca) + 1) % 8)
    # --- project, sort, pair into 4 segment sprites
    sx = s16(m.rw(a2 + 0x1e)); sy = s16(s16(m.rw(a2 + 0x20)) + 7)
    squash = 0
    if m.rb(a2 + 0x1a) == 8:
        anim = m.read32(m.rl(a2 + 0xcc))
        squash = 1 if anim == 0x215c6 else 2 if anim == 0x215ea else 0
    pts = []
    for k in range(8):
        x = s16(m.rw(a2 + 0x74 + 2 * k)); y = s16(m.rw(a2 + 0x84 + 2 * k)); z = s16(m.rw(a2 + 0x94 + 2 * k))
        if squash == 1: z = s16(z - (z >> 2))
        elif squash == 2: z = s16(z + (z >> 1))
        X = s16((sx * 2 + ((y - x) >> 10)) >> 1)
        Y = s16((sy * 2 + ((z - ((y + x) >> 1)) >> 10)) >> 1)
        pts.append([X, Y])
    for i in range(7):                                    # the ROM's exchange sort on X
        for j in range(i + 1, 8):
            if pts[i][0] > pts[j][0]:
                pts[i], pts[j] = pts[j], pts[i]
    for k in range(4):
        (x0, y0), (x1, y1) = pts[2 * k], pts[2 * k + 1]
        dx = s16(x1 - x0); dy = s16(y1 - y0)
        if dx > 2: dx = 2
        ady = min(abs(dy), 12)
        code = m.romw(0x24ade + 2 * (ady * 3 + dx))
        if dy < 0:
            code = (code + 0x8000) & 0xffff
            x0 = s16(x0 + dx - 6); y0 = s16(y0 + dy)
        o = a2 + 0xa4 + 6 * k
        m.ww(o, code); m.ww(o + 2, x0); m.ww(o + 4, y0 + s8(m.romb(0x24b2c + ady)))


# ===========================================================================
# Level objects: 25 slots at 0x400A9C (stride 0x56)
#   +0x0C/+0x10/+0x14 position   +0x18 active   +0x1A state   +0x1C step timer
#   +0x1E render kind (1 = playfield animation, 2 = playfield + sprite)
#   +0x1F type (selects the FUN_29CCE contact handler)
#   +0x36 program counter (bytecode)   +0x3A program start   +0x3E animation frame
#   +0x46 / +0x4A animation scripts    +0x52/+0x54 scroll rows it lives in
# ===========================================================================
LEVEL_SLOTS = [0x400a9c + 0x56 * i for i in range(25)]
BLANK_SCRIPT = 0x20c14


def level_objects_spawn_12dfa(m, old_row, new_row):
    """FUN_12DFA: create level objects whose row range the scroll has just entered
    (table 0x1DEC0 per race: row from, row to, program), remove the ones it left."""
    a2 = m.read32(0x1dec0 + 4 * m.rw(0x400394))
    while m.romb(a2) != 0xff:
        lo, hi, prog = s8(m.romb(a2)), s8(m.romb(a2 + 1)), m.read32(a2 + 2)
        dup = any(m.rb(o + 0x18) == 1 and (m.rl(o + 0x3a) == prog or (prog == 0 and m.rb(o + 0x1f) == 0x0c))
                  for o in LEVEL_SLOTS)
        if not dup and new_row in (lo, hi) and (old_row < lo or old_row > hi):
            progs = [prog]
            if prog == 0:                                  # a random group of four (FUN_13A98)
                g = random_13a98(m, 4)
                progs = [m.read32(0x1ded8 + 16 * g + 4 * k) for k in range(4)]
            full = False
            for pr in progs:
                o = next((o for o in LEVEL_SLOTS if m.rb(o + 0x18) == 0), None)
                if o is None:
                    full = True
                    break
                m.ww(o + 0x52, lo); m.ww(o + 0x54, hi)
                level_object_init_12f44(m, o, 0, pr)
            if full:
                break
        a2 += 6
    for o in LEVEL_SLOTS:
        if m.rb(o + 0x18):
            lo, hi = s16(m.rw(o + 0x52)), s16(m.rw(o + 0x54))
            if (old_row == lo and new_row < lo) or (old_row == hi and new_row > hi):
                level_object_init_12f44(m, o, 1, 0)


def level_object_init_12f44(m, o, remove, prog):
    """FUN_12F44: start an object on a program (state 3), or remove it."""
    if not remove:
        m.wl(o + 0x3a, prog); m.wb(o + 0x1a, 3); m.wb(o + 0x18, 1)
        return
    if m.rl(0x400974) == o:
        m.wl(0x400978, 0); m.wl(0x400974, 0)
    m.wb(o + 0x18, 0); m.wb(o + 0x1a, 0)
    if m.rb(o + 0x1f) == 6:
        m.wb(0x40075c, m.rb(0x40075c) - 1)


def level_object_render_13334(m, o):
    """FUN_13334: hand a playfield animation frame to FUN_1344C (+0x1E = 1 or 2) and
    work out the object's screen position (+0x4E)."""
    kind = m.rb(o + 0x1e)
    if kind in (1, 2):
        if m.read32(m.rl(o + 0x3e)) == 0xffffffff:
            return
        if kind == 1 or m.rb(o + 0x1a) in (1, 2):
            m.wl(0x400970, m.rl(o + 0x3e)); m.wl(0x400974, o)
        if kind != 2:
            return
    x, y = s16(m.rw(o + 0x0c)), s16(m.rw(o + 0x10))
    m.ww(0x400690, x); m.ww(0x400692, y)
    sx = s16(y - x + 0x88)
    sy = s16(s16(m.rw(o + 0x14)) + s16(m.rw(0x40097e)) + 0x54 - ((x + y) >> 1))
    m.wl(o + 0x4e, ((sx & 0xffff) << 16) | (sy & 0xffff))
    if m.rb(o + 0x1f) == 6:
        terrain_cells_1d06a(m, s8(m.rb(o + 0x25)))
    if m.rb(o + 0x1f) == 3:                       # the wave machine's colours (0x1340A)
        d1 = ((m.rl(o + 0x3e) - m.rl(o + 0x46)) & 0xffffffff) >> 3
        if m.rl(o + 0x46) == 0x21192:
            d1 += 7
        m.palette(s8(m.romb(0x1df18 + s16(d1 & 0xffff))))
    m.wl(o + 0x42, m.rl(o + 0x3e))


def level_object_frame_132e0(m, o):
    """FUN_132E0: next animation frame; at the end of the script rewind and count
    down the repeat counter (+0x1C).  Returns 1 when the repeats are used up."""
    m.wl(o + 0x3e, m.rl(o + 0x3e) + (8 if m.rb(o + 0x1e) else 4))
    if m.read32(m.rl(o + 0x3e)) != 0xffffffff:
        return 0
    m.wl(o + 0x3e, m.rl(o + 0x46) if m.rb(o + 0x1a) == 2 else m.rl(o + 0x4a))
    if m.rw(o + 0x1c) == 0:
        return 0
    m.ww(o + 0x1c, m.rw(o + 0x1c) - 1)
    return 1 if m.rw(o + 0x1c) == 0 else 0


def level_object_program_12896(m, o):
    """FUN_12896: run the object's bytecode until an instruction that waits."""
    def word():
        v = m.read16(m.rl(o + 0x36)); m.wl(o + 0x36, m.rl(o + 0x36) + 2); return v

    def long():
        v = m.read32(m.rl(o + 0x36)); m.wl(o + 0x36, m.rl(o + 0x36) + 4); return v
    while True:
        op = s16(word())
        go = True
        if op == 0:                                        # create
            m.wl(o + 0x0c, s16(word()) << 19); m.wl(o + 0x10, s16(word()) << 19)
            m.wl(o + 0x14, s16(word()) << 16)
            m.wb(o + 0x1e, word() & 0xff); m.wb(o + 0x1f, word() & 0xff)
            m.wl(o + 0x42, BLANK_SCRIPT)
            if m.rb(o + 0x1e) == 1:
                m.ww(o + 0x26, word()); m.ww(o + 0x28, word())
                m.wl(o + 0x46, long()); m.wl(o + 0x3e, m.rl(o + 0x46))
                level_object_render_13334(m, o)
            else:
                m.wl(o + 0x3e, BLANK_SCRIPT)
                m.unported.add('FUN_18E6C (display list)')
                if m.rb(o + 0x1f) == 6:
                    m.ww(o + 0x52, 0); m.ww(o + 0x54, 0x500)
                    m.wb(0x40075e, 0); m.wb(0x40075c, m.rb(0x40075c) + 1); m.wb(o + 0x25, 0)
            if m.rb(o + 0x1f) == 3:
                m.wl(o + 0x4e, 0xfffffe00)
                m.wl(0x40044a, 0x211fe); m.wb(0x400456, 2)
                m.wl(0x40044e, 0x2126e); m.wb(0x400458, 3)
                m.wl(0x400452, 0x212b2); m.wb(0x40045a, 3)
        elif op == 1:                                      # move every n frames (state 1)
            m.ww(o + 0x1c, word()); m.wb(o + 0x21, word() & 0xff)
            m.wl(o + 0x3e, m.rl(o + 0x4a)); m.wb(o + 0x20, 0); m.wb(o + 0x1a, 1); go = False
        elif op == 2:                                      # animate every n frames (state 2)
            if m.rb(o + 0x1f) == 0x0a:
                m.sound(0x60)
            m.ww(o + 0x1c, word()); m.wb(o + 0x23, word() & 0xff)
            m.wl(o + 0x3e, m.rl(o + 0x46)); m.wb(o + 0x22, 0); m.wb(o + 0x1a, 2); go = False
        elif op == 3:                                      # wait n updates (state 0)
            m.ww(o + 0x1c, word()); m.wb(o + 0x1a, 0); go = False
        elif op == 4:
            m.wb(o + 0x24, word() & 0xff); m.wl(o + 0x2a, m.rl(o + 0x36))
        elif op == 5:
            if m.rb(o + 0x24):
                m.wb(o + 0x24, m.rb(o + 0x24) - 1)
                if m.rb(o + 0x24):
                    m.wl(o + 0x36, m.rl(o + 0x2a))
            else:
                m.wl(o + 0x36, m.rl(o + 0x2a))
        elif op == 6:
            m.wb(o + 0x25, word() & 0xff); m.wl(o + 0x2e, m.rl(o + 0x36))
        elif op == 7:
            if m.rb(o + 0x25):
                m.wb(o + 0x25, m.rb(o + 0x25) - 1)
                if m.rb(o + 0x25):
                    m.wl(o + 0x36, m.rl(o + 0x2e))
            else:
                m.wl(o + 0x36, m.rl(o + 0x2e))
        elif op == 8:                                      # branch if a marble is within 3 tiles
            hit = False
            if word() == 0:
                for i in range(s16(m.rw(0x400396))):
                    p = 0x400018 + 0xe2 * i
                    if m.rb(p + 0x18) == 1:
                        dx = s16(s32(m.rl(p + 0x0c) - m.rl(o + 0x0c)) >> 19)
                        dy = s16(s32(m.rl(p + 0x10) - m.rl(o + 0x10)) >> 19)
                        if 0 <= dx < 3 and 0 <= dy < 3:
                            hit = True
                            m.sound(0x31)
            if hit:
                m.wl(o + 0x36, m.read32(m.rl(o + 0x36)))
            else:
                m.wl(o + 0x36, m.rl(o + 0x36) + 2)
        elif op == 9:
            m.wl(o + 0x36, m.read32(m.rl(o + 0x36)))
        elif op == 10:
            pc = m.rl(o + 0x36); m.wl(o + 0x36, m.read32(pc)); m.wl(o + 0x32, pc + 4)
        elif op == 11:
            m.wl(o + 0x36, m.rl(o + 0x32))
        elif op == 12:
            v = long(); m.wl(o + 0x46, v); m.wl(o + 0x3e, v)
        elif op == 13:
            v = long(); m.wl(o + 0x4a, v); m.wl(o + 0x3e, v)
        elif op == 14:
            m.wl(o, s16(word()) << 8); m.wl(o + 4, s16(word()) << 8)
        elif op == 15:
            level_object_init_12f44(m, o, 1, 0); go = False
        elif op == 16:
            m.wl(o + 0x0c, m.rl(o + 0x0c) + m.rl(o)); m.wl(o + 0x10, m.rl(o + 0x10) + m.rl(o + 4))
        elif op == 17:
            m.wl(o + 0x32, m.rl(o + 0x36) - 2); m.wb(o + 0x1a, 4); m.wb(o + 0x1b, word() & 0xff); go = False
        elif op == 18:
            d2 = True
            for i in range(s16(m.rw(0x400396))):
                p = 0x400018 + 0xe2 * i
                if m.rb(p + 0x18) == 1:
                    if m.rb(o + 0x1b) == m.rb(p + 0x1b):
                        d2 = False
                    elif s8(m.rb(p + 0x1b)) == s8(m.rb(o + 0x1b)) + 1 and s8(m.rb(p + 0x1b)) >= 0x1e:
                        d2 = False
            if d2:
                m.wl(o + 0x36, m.read32(m.rl(o + 0x36)))
                m.sound(m.read32(0x1ef5a + 4 * (s8(m.rb(o + 0x1b)) - 30)))   # vacuum hum off
            else:
                m.wl(o + 0x36, m.rl(o + 0x36) + 4)
        else:
            go = False
        if not go:
            return


def level_object_update_13068(m, o):
    """FUN_13068: one level object's update (states 0-4)."""
    if m.rb(o + 0x18) == 0:
        return
    old = m.rb(o + 0x1a)
    if m.rb(o + 0x1f) == 3:                          # the three parts that animate on their own
        m.wb(0x400456, m.rb(0x400456) - 1)
        if m.rb(0x400456) == 0:
            m.wb(0x400456, 2)
            p = (m.rl(0x40044a) + 4) & 0xffffffff
            m.wl(0x40044a, p)
            if m.read32(p) == 0xffffffff:
                if m.read32(p - 4) == 0x211f2:
                    m.wl(0x40044a, 0x2121e); m.wb(0x400456, m.rb(0x400456) + 15)
                else:
                    m.wl(0x40044a, 0x211fe); m.wb(0x400456, m.rb(0x400456) + 30)
        for cnt, ptr, reset in ((0x400458, 0x40044e, 0x2126e), (0x40045a, 0x400452, 0x212b2)):
            m.wb(cnt, m.rb(cnt) - 1)
            if m.rb(cnt) == 0:
                m.wb(cnt, 3)
                p = (m.rl(ptr) + 4) & 0xffffffff
                m.wl(ptr, p)
                if m.read32(p) == 0xffffffff:
                    m.wl(ptr, reset)
    d2 = False
    while True:
        st = m.rb(o + 0x1a)
        if st == 0:
            if m.rw(o + 0x1c):
                m.ww(o + 0x1c, m.rw(o + 0x1c) - 1)
                if m.rw(o + 0x1c) == 0 and m.rb(o + 0x18) == 1:
                    level_object_program_12896(m, o)
            d2 = True
        elif st == 1:
            if m.rb(o + 0x21):
                m.wb(o + 0x20, m.rb(o + 0x20) + 1)
            if m.rb(o + 0x21) == m.rb(o + 0x20):
                m.wb(o + 0x20, 0)
                m.wl(o + 0x0c, m.rl(o + 0x0c) + m.rl(o)); m.wl(o + 0x10, m.rl(o + 0x10) + m.rl(o + 4))
                level_object_render_13334(m, o)
                if level_object_frame_132e0(m, o):
                    level_object_program_12896(m, o)
            else:
                d2 = True
        elif st == 2:
            if m.rb(o + 0x23):
                m.wb(o + 0x22, m.rb(o + 0x22) + 1)
            if m.rb(o + 0x23) == m.rb(o + 0x22):
                m.wb(o + 0x22, 0)
                if m.rb(o + 0x1f) == 6:
                    m.wb(o + 0x25, m.rb(o + 0x25) + 1)
                    if m.rb(o + 0x25) == 0x1e:
                        m.wb(0x40075e, 1)
                if m.rb(o + 0x1f) == 0x19:
                    k = ((m.rl(o + 0x3e) - m.rl(o + 0x46)) >> 2) >> 1
                    m.ww(0x40045c, s16(k * 0x90))
                level_object_render_13334(m, o)
                if level_object_frame_132e0(m, o):
                    level_object_program_12896(m, o)
            else:
                d2 = True
        elif st == 3:
            m.wl(o + 0x36, m.rl(o + 0x3a))
            m.wl(o + 0x4a, BLANK_SCRIPT); m.wl(o + 0x46, BLANK_SCRIPT); m.wl(o + 0x3e, BLANK_SCRIPT)
            level_object_program_12896(m, o)
        elif st == 4:
            m.wl(o + 0x3e, BLANK_SCRIPT); d2 = True
        if m.rb(o + 0x1a) != old and m.rb(o + 0x1a) == 1 and m.rb(o + 0x21) == 0:
            old = m.rb(o + 0x1a)
            continue
        break
    if m.rb(o + 0x1e) != 1 and d2:
        level_object_render_13334(m, o)


def playfield_animation_1344c(m):
    """FUN_1344C (once a frame): apply the pending playfield-animation frame.  The
    tile redraw half is drawn by the front end; this ports the half that matters
    to the marble: the frame's height-cell patch (start index, count, codes),
    copied into the dynamic cell table at 0x40076E."""
    frame = m.rl(0x400970)
    if not frame:
        return
    obj = m.rl(0x400974)
    if m.rb(obj + 0x1e) == 1:                         # the tile block this frame shows
        if m.rb(obj + 0x1f) == 0x19:                  # Ultimate: 30 whole rows swapped in
            if not hasattr(m, 'pf_patches'):           # from further down the row table
                m.pf_patches = []
            m.pf_patches.append((s16(m.rw(obj + 0x28)), s16(m.rw(obj + 0x26)), 36, 30,
                                 ('rows', m.read32(frame) & 0xffff)))
        else:
            blk = m.read32(frame)
            w, h = m.romb(blk), m.romb(blk + 1)
            codes = [m.read16(blk + 2 + 2 * k) for k in range(w * h)]
            # level tile row +0x28, column +0x26 (the ROM writes the rows on screen
            # into playfield RAM; the scroll routine redraws the rest the same way)
            if not hasattr(m, 'pf_patches'):
                m.pf_patches = []
            m.pf_patches.append((s16(m.rw(obj + 0x28)), s16(m.rw(obj + 0x26)), w, h, codes))
        m.wl(0x400978, frame)
    patch = m.read32(frame + 4)
    if patch:
        start, count = s16(m.read16(patch)), m.read16(patch + 2)
        for k in range(count):
            m.ww(0x40076e + 2 * (start + k), m.read16(patch + 4 + 2 * k))
    m.wl(0x400970, 0)


def level_objects_update_12fd0(m):
    """FUN_12FD0: (Intermediate's special zone), dynamic cells for type-6 objects,
    then every level object.  (The frame they pick is applied by FUN_1344C at the
    start of the next tick, from the scroll routine.)"""
    if m.rw(0x400394) == 2:                            # Intermediate: a marble in zone 9 or 10
        for i in range(s16(m.rw(0x400396))):          # (while 0x40075E is set) starts 0x1D854
            p = 0x400018 + 0xe2 * i
            if m.rb(p + 0x18) == 1 and m.rb(0x40075e) and m.rb(p + 0x1b) in (9, 10):
                o = next((o for o in LEVEL_SLOTS if m.rb(o + 0x18) == 0), None)   # FUN_12D6E
                if o is not None:                                                # FUN_12D46
                    level_object_init_12f44(m, o, 0, 0x1d854)
                break
    if m.rb(0x40075c):
        init_dynamic_cells_11ac2(m)
    for o in LEVEL_SLOTS:
        level_object_update_13068(m, o)


# --- FUN_29CCE: marble against the level objects ---------------------------------
_WHITELIST = {0x00, 0x10, 0x3b, 0x17, 0x18, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37,
              0x2d, 0x2e, 0x38, 0x39, 0x3a, 0x2f, 0x30, 0x31}
_EPI = {0x10, 0x17, 0x18, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37}


def _wall(m, along, across, vel_along, flag_along, flag_across, a_lo, a_hi, c_lo, c_hi, end_lo, end_hi):
    """The invisible wall boxes (types 0x1A-0x1F): inside the box, bounce across;
    near either end and moving outwards, bounce along too."""
    if not (a_lo < along < a_hi and c_lo < across < c_hi):
        return
    lo0, lo1 = end_lo
    hi0, hi1 = end_hi
    if (lo0 < along < lo1 and vel_along < 0) or (hi0 < along < hi1 and vel_along > 0):
        m.wb(flag_along, 1)
    m.wb(flag_across, 1)
    m.sound(0x42)                                          # bumped an invisible wall


def plane_collide_1cd00(m, a2, obj, shape):
    """FUN_1CD00: the marble against a solid shape (planes, table 0x24C5E): each
    plane has a normal (3 words; one > 0x800 ends the list), a box (6 bytes) and a
    point.  Crossing a plane slides the marble along it; already touching it, the
    marble bounces straight back - or breaks if it is falling from high enough.
    Returns True when the marble broke (the caller then stops)."""
    if shape == 0xff:
        return False
    v = [s32(m.rl(a2 + k)) for k in (0, 4, 8)]
    ox, oy, oz = s16(m.rw(obj + 0x0c) + 8), s16(m.rw(obj + 0x10) + 8), s16(m.rw(obj + 0x14))
    now = (s16(ox - m.rw(0x400690)), s16(oy - m.rw(0x400692)), s16(oz - m.rw(0x400694)))
    prev = (s16(ox - m.rw(0x400684)), s16(oy - m.rw(0x400688)), s16(oz - m.rw(0x40068c)))
    a0 = m.read32(0x24c5e + 4 * s8(shape))
    while True:
        n0 = s16(m.romw(a0)); last = n0 > 0x800
        if last:
            n0 = s16(n0 - 0x1000)
        n1, n2 = s16(m.romw(a0 + 2)), s16(m.romw(a0 + 4))
        box = [s8(m.romb(a0 + 6 + k)) for k in range(6)]
        pt = [s8(m.romb(a0 + 12 + k)) for k in range(3)]
        inside = lambda p: all(box[2 * k] <= p[k] <= box[2 * k + 1] for k in range(3))
        if inside(now) or inside(prev):
            dist = lambda p: (n2 * s16(p[2] - pt[2]) + (n1 * s16(p[1] - pt[1]) + n0 * s16(p[0] - pt[0])))
            d5, d6 = s32(dist(now)), s32(dist(prev))
            if abs(d5) >= 0x400 and abs(d6) >= 0x400 and (d5 ^ d6) >= 0:
                pass                                       # both on the same side: clear
            elif abs(d6) < 0x400:                          # was touching it already
                if m.rb(a2 + 0x36) == 2 and abs(s32(m.rl(a2 + 0x14) - m.rl(a2 + 0x2a))) > 0x100000:
                    stop_roll_15884(m); m.sound(0x46)
                    if a2 in (0x400018, 0x4000fa):
                        m.wb(a2 + 0x57, 0x64)
                        state_entry_25bae(m, a2, 4)
                    else:
                        remove_object_15bd0(m, a2, award=True, remove=True)
                    return True
                for k, o in enumerate((0, 4, 8)):
                    m.wl(a2 + o, (-v[k]) & 0xffffffff)
                m.wl(a2 + 0x0c, m.rl(0x400684)); m.wl(a2 + 0x10, m.rl(0x400688)); m.wl(a2 + 0x14, m.rl(0x40068c))
                return False
            else:                                          # crossing it: back, and slide along
                m.wl(a2 + 0x0c, m.rl(0x400684)); m.wl(a2 + 0x10, m.rl(0x400688)); m.wl(a2 + 0x14, m.rl(0x40068c))
                vx, vy, vz = (s32(m.rl(a2 + o)) for o in (0, 4, 8))
                dot = s16(s32(s16(vx >> 7) * n0 + n1 * s16(vy >> 7) + n2 * s16(vz >> 7)) >> 8)
                m.wl(a2, (vx - n0 * dot) & 0xffffffff)
                m.wl(a2 + 4, (vy - n1 * dot) & 0xffffffff)
                m.wl(a2 + 8, (vz - n2 * dot) & 0xffffffff)
        if last:
            return False
        a0 += 16


def _pipe(m, a2, o, t, d6, a0):
    """FUN_29CCE handlers 0x12-0x15: the pipes.  0x12 (Beginner) and 0x13/0x14 are
    mouths: roll in and the marble comes out at a fixed place, rolling on, after
    18 updates (state 3, hidden while +0x59 counts); their outside and 0x15 are
    solid shapes (FUN_1CD00).  Returns True if the routine stops there."""
    d1 = s16((s16(m.rw(o + 0x0c)) >> 3) - s16(m.rw(0x400696)))
    d2 = s16((s16(m.rw(o + 0x10)) >> 3) - s16(m.rw(0x400698)))
    z = s16(m.rw(0x400694))
    player = a2 in (0x400018, 0x4000fa)

    def come_out(x, y_free, y_taken, vx, vy, xfix=None):
        m.wl(a2, vx); m.wl(a2 + 4, vy)
        if xfix is None:                                   # 0x12: X depends on the spot being free
            taken = occupied_17cb8(m, a2, x, y_free, 0x70)
            m.wl(a2 + 0x0c, ((0x204 if taken else x) << 16) & 0xffffffff)
            m.wl(a2 + 0x10, (y_free << 16) & 0xffffffff)
        else:
            taken = occupied_17cb8(m, a2, xfix, y_free, 0x70)
            m.wl(a2 + 0x0c, (xfix << 16) & 0xffffffff)
            m.wl(a2 + 0x10, ((y_taken if taken else y_free) << 16) & 0xffffffff)
        sprite_pos_update_1bab2(m, a2)
        m.wl(a2 + 0x14, sprite_project_1cc62(m, 0))
        stop_roll_15884(m); m.sound(0x35)                  # into the pipe

    if t == 0x12:
        if not (-2 <= d1 < 3 and -1 <= d2 < 5 and z > 0x3f40):
            return False
        if 0 <= d1 < 3 and 0 <= d2 < 4:
            m.wb(a2 + 0x58, 0x12); m.wb(a2 + 0x59, 0xff)
        if z >= 0x3f78:
            return plane_collide_1cd00(m, a2, o, 0)
        if d1 < 0:
            return False
        if z >= 0x3f60:
            m.wl(a2 + 4, 0); m.wl(a2, 0)
            return False
        if player:
            m.wb(a2 + 0x36, 0); m.wb(a2 + 0x59, 0x12)
            come_out(0x20c, 0x264, None, 0x40000, 0)
            m.wb(a2 + 0x1a, 3)
        else:
            remove_object_15bd0(m, a2, award=True, remove=True)
        return False
    if t in (0x13, 0x14):
        if not (-24 <= d6 < 32 and -16 <= a0 < 24):
            return False
        if d2 == 0 and 0 <= d1 < 3:
            m.wb(a2 + 0x59, 0x12)
            come_out(None, 0x2e4, 0x2dc, 0, 0x40000, xfix=0x29c)
            m.wb(a2 + 0x58, t); m.wb(a2 + 0x1a, 3)
            return False
        return plane_collide_1cd00(m, a2, o, 2)
    if t == 0x20:                                          # Intermediate: the mouth near the top
        if not (-2 <= d1 < 3 and -1 <= d2 < 5 and z > 0x3f80):
            return False
        if 0 <= d1 < 3 and 0 <= d2 < 4:
            m.wb(a2 + 0x58, t); m.wb(a2 + 0x59, 0xff)
        if z >= 0x3fb0:
            return plane_collide_1cd00(m, a2, o, 0)
        if d1 < 0:
            return False
        if z >= 0x3fa0:
            m.wl(a2 + 4, 0); m.wl(a2, 0)
            return False
        m.wb(a2 + 0x36, 0); m.wb(a2 + 0x59, 0x12)
        m.wl(a2, 0); m.wl(a2 + 4, 0xfffc0000)
        y = 0x25c if occupied_17cb8(m, a2, 0x26c, 0x254, 0x70) else 0x254
        m.wl(a2 + 0x0c, 0x26c << 16); m.wl(a2 + 0x10, y << 16)
        sprite_pos_update_1bab2(m, a2)
        m.wl(a2 + 0x14, sprite_project_1cc62(m, 0))
        m.wb(a2 + 0x58, t); m.wb(a2 + 0x1a, 3)
        stop_roll_15884(m); m.sound(0x35)
        return False
    if t == 0x21:                                          # its outside: solid shape 4
        if -1 <= d1 < 4 and -1 <= d2 < 3 and z < 0x3f60:
            return plane_collide_1cd00(m, a2, o, 4)
        return False
    if t == 0x22:                                          # Intermediate: the lower mouth
        if d2 != 1 or d1 != 1:
            return False
        if z <= 0x3f94:                                    # dropping in
            if z == 0x3f74:
                m.wl(a2 + 0x14, (m.rl(a2 + 0x14) + 0x20000) & 0xffffffff)
            m.wl(a2 + 8, (m.rl(a2 + 8) + 0x9000) & 0xffffffff)
            m.wl(a2 + 4, 0); m.wl(a2, 0)
            m.wb(a2 + 0x36, 2)
            return False
        m.wb(a2 + 0x36, 0); m.wb(a2 + 0x59, 0x12)
        m.wl(a2, 0); m.wl(a2 + 4, 0xfffc0000)
        if random_13a98(m, 2):
            x = 0x2ac if occupied_17cb8(m, a2, 0x2dc, 0x2b0, 0x70) else 0x2dc
        else:
            x = 0x2dc if occupied_17cb8(m, a2, 0x2ac, 0x2b0, 0x70) else 0x2ac
        m.wl(a2 + 0x0c, x << 16); m.wl(a2 + 0x10, 0x2b0 << 16)
        sprite_pos_update_1bab2(m, a2)
        z = sprite_project_1cc62(m, 0)
        m.wl(a2 + 0x14, z); m.wl(0x40068c, z)
        m.wb(a2 + 0x58, t); m.wb(a2 + 0x1a, 3)
        stop_roll_15884(m); m.sound(0x36)
        return False
    if not (-24 <= d6 < 40 and -24 <= a0 < 40 and z < 0x3f40):     # 0x15
        return False
    return plane_collide_1cd00(m, a2, o, 1)


def level_object_contacts_29cce(m, a2):
    FX, FY = 0x400666, 0x400668
    vx0, vy0 = s32(m.rl(a2)), s32(m.rl(a2 + 4))
    mx, my = s16(m.rw(0x400690)), s16(m.rw(0x400692))
    d3 = m.rb(a2 + 0x58)
    m.wb(a2 + 0x58, 0)
    for o in LEVEL_SLOTS:
        if m.rb(o + 0x18):
            d6 = s16(m.rw(o + 0x0c) - mx)                  # object minus marble, X
            a0 = s16(m.rw(o + 0x10) - my)                  # and Y
            t = s8(m.rb(o + 0x1f))
            if t == 0x05:                                  # a post (the goal flagpoles)
                ax, ay = (abs(d6) << 4) & 0xffff, (abs(a0) << 4) & 0xffff
                d = ((ay >> 3) * 3 + ax) & 0xffff if ax > ay else ((ax >> 3) * 3 + ay) & 0xffff
                if d < 56:
                    m.wb(FY, 1); m.wb(FX, 1)
                    m.sound(0x42)
            elif t == 0x1b:
                _wall(m, d6, a0, vx0, FX, FY, -8, 32, -12, 0, (-8, 0), (24, 32))
            elif t == 0x1a:
                _wall(m, a0, d6, vy0, FY, FX, -8, 32, -12, 0, (-8, 0), (24, 32))
            elif t == 0x1d:
                _wall(m, a0, d6, vy0, FY, FX, -8, 32, -8, 4, (-8, 0), (24, 32))
            elif t == 0x1c:
                _wall(m, d6, a0, vx0, FX, FY, -8, 32, -8, 4, (-8, 0), (24, 32))
            elif t == 0x1f:
                _wall(m, a0, d6, vy0, FY, FX, -12, 84, -14, 0, (-12, -4), (76, 84))
            elif t == 0x1e:
                _wall(m, d6, a0, vx0, FX, FY, -12, 94, -14, 0, (-12, -4), (86, 94))
            elif t in (0x06, 0x07, 0x08, 0x09, 0x0e, 0x0f, 0x11, 0x19, 0x28, 0x29, 0x2a, 0x2b, 0x2c):
                pass                                       # no contact handler (ROM 0x2B072)
            elif t == 0x10:                                # an area that marks the marble
                d1 = s16((s16(m.rw(o + 0x0c)) >> 3) - s16(m.rw(0x400696)))
                d2 = s16((s16(m.rw(o + 0x10)) >> 3) - s16(m.rw(0x400698)))
                if 0 <= d1 < 16 and 0 <= d2 < 14:
                    m.wb(a2 + 0x58, 0x10); m.wb(a2 + 0x59, 0xff)
            elif t == 0x16:                                # a pipe outlet: solid shape 3
                if -16 <= d6 < 32 and -16 <= a0 < 24 and plane_collide_1cd00(m, a2, o, 3):
                    return
            elif t in (0x0a, 0x0b, 0x0c, 0x0d, 0x17, 0x18):  # Aerial: cup, vacuums, bumpers, markers
                if _aerial_contact(m, a2, o, t, d6, a0, vx0, vy0, FX, FY):
                    return
            elif t in _MARK_AREAS:                          # Ultimate: areas that mark the marble
                w, h, same_z = _MARK_AREAS[t]
                d1 = s16((s16(m.rw(o + 0x0c)) >> 3) - s16(m.rw(0x400696)))
                d2 = s16((s16(m.rw(o + 0x10)) >> 3) - s16(m.rw(0x400698)))
                if 0 <= d1 < w and 0 <= d2 < h and (not same_z or m.rl(o + 0x14) == m.rl(a2 + 0x14)):
                    m.wb(a2 + 0x58, t); m.wb(a2 + 0x59, 0xff)
            elif t in (0x23, 0x24, 0x26, 0x27):             # Ultimate: solid shapes 5 and 6
                if t < 0x25:
                    near = -16 <= d6 < 32 and -24 <= a0 < 40
                else:
                    near = -24 <= d6 < 40 and -24 <= a0 < 40
                if near and plane_collide_1cd00(m, a2, o, 5 if t < 0x25 else 6):
                    return
            elif t == 0x25:                                 # Ultimate: the hole (0x2A87A)
                _ultimate_hole(m, a2, o)
            elif t in (0x12, 0x13, 0x14, 0x15, 0x20, 0x21, 0x22):   # the pipes
                if _pipe(m, a2, o, t, d6, a0):
                    return                                 # (ROM 0x2B228: straight out)
            elif 5 <= t <= 0x3b:
                m.unported.add(f'FUN_29CCE handler for level object type {t:#x}')
        if m.rb(a2 + 0x58) not in _WHITELIST:
            break
    cls = (0x10, 0x17, 0x18, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37)    # (ROM 0x2B108)
    if m.rb(a2 + 0x58) in cls and d3 not in cls:
        m.sound(0x43)
    elif m.rb(a2 + 0x58) not in cls and d3 in cls:
        m.sound(0x44)
    if m.rb(FX):
        m.wl(a2 + 0x0c, m.rl(0x400684)); m.wl(a2, -s32(m.rl(a2)))
    if m.rb(FY):
        m.wl(a2 + 0x10, m.rl(0x400688)); m.wl(a2 + 4, -s32(m.rl(a2 + 4)))

# ---------------------------------------------------------------------------
# FUN_121B8 assembled (main physics).  Routines not yet ported are flagged in
# m.unported and skipped: FUN_29CCE, FUN_1BC88, FUN_160F6, the player-only
# object/enemy checks, the sprite bookkeeping, and landing-event bookkeeping.
# ---------------------------------------------------------------------------
def stop_roll_15884(m):
    """FUN_15884: 0x3A (stop the rolling sound 0x39), then 0x3B unless it is the
    Intermediate Race."""
    m.sound(0x3a)
    if m.rw(0x400394) != 2:
        m.sound(0x3b)


def physics_121b8(m, a2):
    player = a2 in (0x400018, 0x4000fa)
    if not physics_prefix_121b8(m, a2):                # embedded in a wall: lost
        if player:
            stop_roll_15884(m); m.sound(0x46)
            m.wb(a2 + 0x57, 0x65)
            state_entry_25bae(m, a2, 4)
        else:
            remove_object_15bd0(m, a2, award=True, remove=True)
        return
    if m.rb(0x400666) or m.rb(0x400668):
        sprite_pos_update_1bab2(m, a2)
    m.wb(0x400668, 0); m.wb(0x400666, 0)
    level_object_contacts_29cce(m, a2)
    if m.rb(a2 + 0x58) == 0x0a or m.rb(a2 + 0x1a) == 4:
        return
    if m.rb(0x400666) or m.rb(0x400668):              # pushed back by a level object: the
        sprite_pos_update_1bab2(m, a2)                 # cell it is really in (ROM 0x12384)
    m.wb(0x400668, 0); m.wb(0x400666, 0)
    if contact_1bc88(m, a2):
        sprite_pos_update_1bab2(m, a2)
    if player:
        if hazard_contact_14e92(m, a2):
            sprite_pos_update_1bab2(m, a2)
        if slinky_contact_175c8(m, a2):
            sprite_pos_update_1bab2(m, a2)
        if popups_contact_1881c(m, a2):
            sprite_pos_update_1bab2(m, a2)
        pit_contact_1924e(m, a2)                     # the Silly Race's pit creatures
        drops_contact_19d94(m, a2)                   # and its dropped ones
        if m.rb(a2 + 0x1a) == 0x0b:
            return
        zone_lines_1365c(m, a2)
    floor = sprite_project_1cc62(m, 0)
    edges_160f6(m, a2, floor)
    # --- start of a fall: big height change while not rising
    dz = abs(s32(m.rl(a2 + 0x14) - m.rl(0x40068c)))
    if dz > 0x80000 and m.rb(a2 + 0x58) == 0 and s32(m.rl(a2 + 8)) <= 0:
        m.wb(a2 + 0x36, 2)
        m.ww(a2 + 0x2e, m.rw(0x400696)); m.ww(a2 + 0x30, m.rw(0x400698))
        m.wl(a2 + 8, 0xffffa000)
        m.sound(0x45)                                  # falling
    if m.rb(a2 + 0x36) == 2:
        z = m.rl(a2 + 0x14)
        if s32(floor) < s32(z):                        # still above the floor: falling
            if s32(m.rl(a2 + 0x2a) - z) > 0x800000:    # fallen more than 128 px: broken
                if player:
                    stop_roll_15884(m); m.sound(0x46)
                    m.wb(a2 + 0x57, 0x64)
                    state_entry_25bae(m, a2, 4)
                else:
                    remove_object_15bd0(m, a2, award=True, remove=True)   # fell off the course
                return
        else:                                          # landed
            m.wb(a2 + 0x36, 0); m.wl(a2 + 8, 0)
            drop = abs(s32(m.rl(a2 + 0x2a) - floor))
            m.wl(a2 + 0x14, floor)
            landing_25c74(m, a2, (drop >> 15) & 0xffff)
            m.sound(0x46); m.sound(0x5d)               # falling stops; the landing
            if m.rw(0x400394) == 3 and m.rb(a2 + 0x1b) == 4:
                m.sound(0x10)                          # (Aerial: landed in zone 4)
            if player and m.rw(0x400394) == 0 and m.rb(a2 + 0x1b) in (1, 2, 3):
                practice_jump_bonus(m, a2)
    elif m.rb(a2 + 0x36) == 0:
        m.wl(a2 + 0x14, floor)                         # roll along the floor
    screen_pos_1b9cc(m, a2)
    if player and m.rb(a2 + 0x1a) not in (1, 5):
        stripe_1c014(m, a2)
    sprite_list_1281c(m, a2)
    position_update_1706c(m, a2)
    if m.rb(a2 + 0x36):
        return
    cmd = m.rb(a2 + 0x58)
    if cmd in (0x2d, 0x2e, 0x3b): mode = 2
    elif cmd in (0x38, 0x39, 0x3a): mode = 4
    elif cmd in (0x2f, 0x30, 0x31): mode = 3
    elif cmd in (0x10, 0x17, 0x18, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37): mode = None
    else: mode = 0
    if mode is not None:
        vector_scale_25e7c(m, a2, mode)                # friction / surface drag
    if player and m.rw(0x400390) != 1:
        slope_pull_25df6(m, a2)


def marble_update(m, a2=MARBLE):
    """The marble's per-update chain, as dispatched by FUN_253EC (state 0)."""
    helper_253bc(m, a2)
    object_step_17f66(m, a2)
    physics_121b8(m, a2)


# ---------------------------------------------------------------------------
# The marble's own animations: FUN_25FC2 (frame sequencer), FUN_13ADE (the
# reappearing fragments) and FUN_253EC (the per-state handler that runs them).
# ---------------------------------------------------------------------------
SHATTER_FRAMES = 0x20FDE        # frame list of the break-up (mode 2)
BROOM_FRAMES = 0x215F6          # the broom that sweeps the pieces away (+0x62, on with +0x67)


def anim_25fc2(m, a2):
    """FUN_25FC2: step the frame list at +0x5A (one frame every +0x60 updates, +0x5F
    counts).  In the break-up (mode 2) the broom (+0x62) starts at frame 9 and then
    moves on one frame every update.  At the end of the list: a wobble (modes 1/5)
    starts its loop again; the break-up ends in reappearing (mode 4) - or, for a
    marble out of the game (+0x18 = 2), in its removal."""
    tbl = SHATTER_FRAMES
    ptr = m.rl(a2 + 0x5a)
    if m.rb(a2 + 0x1a) == 2 and tbl < ptr < tbl + 0x80 and ((ptr - tbl) >> 2) > 9:
        m.wb(a2 + 0x66, m.rb(a2 + 0x66) + 1)
        if m.rb(a2 + 0x66) == 1:
            m.wb(a2 + 0x66, 0)
            m.wl(a2 + 0x62, m.rl(a2 + 0x62) + 4)
    m.wb(a2 + 0x5f, m.rb(a2 + 0x5f) + 1)
    if s8(m.rb(a2 + 0x60)) > s8(m.rb(a2 + 0x5f)):
        return
    m.wb(a2 + 0x5f, 0)
    ptr = (m.rl(a2 + 0x5a) + 4) & 0xffffffff
    m.wl(a2 + 0x5a, ptr)
    mode = m.rb(a2 + 0x1a)
    if mode == 2 and ((ptr - tbl) >> 2) & 0xffff == 9:
        m.wl(a2 + 0x62, BROOM_FRAMES); m.wb(a2 + 0x66, 0); m.wb(a2 + 0x67, 1)
        m.sound(0x5f)                                  # the broom sweeps up the pieces
    if m.read32(ptr) != 0xffffffff:
        return
    if mode in (1, 5):
        m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x60, 2)
        m.wl(a2 + 0x5a, 0x20fb6 if s8(m.rb(a2 + 0x56)) > 6 else 0x20fd2)
    elif mode == 2:
        if m.rb(a2 + 0x56) == 0:
            m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x60, 2); m.wl(a2 + 0x5a, 0x20fd2)
            m.wb(a2 + 0x56, 1)
        elif m.rb(a2 + 0x56) == 1:
            stop_roll_15884(m)                         # (0x260F6)
            state_entry_25bae(m, a2, 2)
        else:
            m.wb(a2 + 0x67, 0)
            if a2 in (0x400018, 0x4000fa):
                m.ww(a2 + 0xa4, 0)
            if m.rb(a2 + 0x18) == 2:                 # out of the game
                m.wb(a2 + 0x18, 0)
                m.unported.add('FUN_18F46 (player removed)')
            else:
                m.wb(a2 + 0x57, 0x65)
                state_entry_25bae(m, a2, 4)
    else:
        m.wb(a2 + 0x57, 0x65)
        state_entry_25bae(m, a2, 4)


def _cos_quadrant(m, d3):
    """The four-quadrant cosine/sine pair of FUN_13ADE (angle 0..401, table 0x1EDA2)."""
    t = lambda i: s16(m.romw(0x1eda2 + 2 * i))
    if d3 <= 100:
        return t(d3), t(100 - d3)
    if d3 < 0xc9:
        return s16(-t(0xc8 - d3)), t(d3 - 100)
    if d3 <= 0x12d:
        return s16(-t(d3 - 0xc9)), s16(-t(0x12d - d3))
    return t(0x191 - d3), s16(-t(d3 - 0x12d))


def reappear_13ade(m, a0):
    """FUN_13ADE: the marble coming back - its 8 fragments (tiles 0x10B..0x112)
    spiral in from above onto the respawn point.  The countdown +0x57 (0x64 -> 48
    updates, 0x65 -> 24, 0x66 -> 36) is the height above the marble, half of it the
    radius; the angle +0x2E turns 10 a tick (402 = a full turn), the fragments 50
    apart.  They go into the stripe slots (+0xA4) and the mask slots (+0x38).
    Returns True when the countdown is over."""
    start = {100: 0x30, 101: 0x18, 102: 0x24}.get(s8(m.rb(a0 + 0x57)))
    if start is not None:
        m.wb(a0 + 0x57, start); m.ww(a0 + 0x2e, 0)
    d1 = s8(m.rb(a0 + 0x57))
    m.wb(a0 + 0x57, m.rb(a0 + 0x57) - 1)
    if m.rb(a0 + 0x1a) == 0x0b:
        d1 = 36 - d1
    count = s16(d1)
    radius = count >> 1
    d3 = s16(m.rw(a0 + 0x2e)) + 10
    if d3 >= 0x192:
        d3 -= 0x192
    m.ww(a0 + 0x2e, d3 & 0xffff)
    sx, sy = s16(m.rw(a0 + 0x1e)), s16(m.rw(a0 + 0x20))
    for k in range(4):                                   # (the ROM clears 4 of each)
        m.ww(a0 + 0x38 + 6 * k, 0); m.ww(a0 + 0xa4 + 6 * k, 0)
    used = 0
    for k in range(8):
        c, s_ = _cos_quadrant(m, d3)
        a1 = s16((radius * c) >> 12)
        d0 = s16((radius * s_) >> 12)
        x = s16(s8(m.romb(0x1ef32 + 2 * k)) + s16(sx + d0 - a1))
        y = s16(s8(m.romb(0x1ef33 + 2 * k)) + s16(count + sy - ((d0 + a1) >> 1)))
        if -8 < x < 0x120 and -8 < y < 0xf0:
            slot = a0 + 0xa4 + 6 * used if used < 4 else a0 + 0x38 + 6 * (used - 4)
            used += 1
            m.ww(slot, 0x10b + k); m.ww(slot + 2, x & 0xffff); m.ww(slot + 4, y & 0xffff)
        d3 += 0x32
        if d3 >= 0x192:
            d3 -= 0x192
    m.wb(a0 + 0x1c, 1)
    return m.rb(a0 + 0x57) == 0


def marble_state_253ec(m, a2):
    """FUN_253EC: one update of a player's marble, by state (+0x1A)."""
    mode = s8(m.rb(a2 + 0x1a))
    if m.rb(a2 + 0xd8) and mode not in (2, 4, 10, 11, 7):      # +0xD4 effect wobbling sideways
        m.wb(a2 + 0x68, m.rb(a2 + 0x68) + m.rb(a2 + 0x69))
        if s8(m.rb(a2 + 0x68)) > 2:
            m.wb(a2 + 0x68, 2); m.wb(a2 + 0x69, 0xff)
        elif s8(m.rb(a2 + 0x68)) < -0x16:
            m.wb(a2 + 0x68, 0xea); m.wb(a2 + 0x69, 1)
        if m.rb(a2 + 0x68) & 1:
            m.wb(a2 + 0x70, m.rb(a2 + 0x70) + 1)
            if s8(m.rb(a2 + 0x70)) > 0x28:
                m.wb(a2 + 0x70, 0xff); m.wb(a2 + 0xd8, 0)
    if mode == 0:
        if m.rb(a2 + 0xcb) and not m.rb(a2 + 0x36) and m.rb(a2 + 0x58) in (0, 0x35, 0x34):
            m.wb(a2 + 0x56, 9); m.wb(a2 + 0x57, 10); m.wb(a2 + 0xcb, 0)
            m.wb(a2 + 0x1a, 8); m.wl(a2 + 0xcc, 0x216aa); m.wb(a2 + 0xd0, 0); m.wb(a2 + 0xd1, 1)
            m.sound(0x53)
            return
        marble_update(m, a2)
    elif mode in (1, 5):
        anim_25fc2(m, a2)
        marble_update(m, a2)
        if s8(m.rb(a2 + 0x1a)) != mode:
            return
        if mode == 1:
            if m.rb(a2 + 0x57):
                m.wb(a2 + 0x57, m.rb(a2 + 0x57) - 1)
            if s8(m.rb(a2 + 0x57)) >= s8(m.rb(a2 + 0x56)):
                m.wb(a2 + 0x56, m.rb(a2 + 0x56) + 1)
                return
            m.wb(a2 + 0x56, m.rb(a2 + 0x56) - 1)
        else:
            if m.rb(a2 + 0x56):
                m.wb(a2 + 0x56, m.rb(a2 + 0x56) - 1)
        if m.rb(a2 + 0x56) == 0:                   # the wobble is over (0x25796 / 0x257F6)
            stop_roll_15884(m)
            m.wb(a2 + 0x1a, 0); m.wl(a2 + 0x5a, 0)
            stripe_1c014(m, a2)
    elif mode == 2:
        anim_25fc2(m, a2)
        screen_pos_1b9cc(m, a2, True)
        if m.rb(a2 + 0x1c):
            sprite_list_1281c(m, a2)
    elif mode == 3:
        screen_pos_1b9cc(m, a2, True)
        stripe_1c014(m, a2)
        sprite_list_1281c(m, a2)
        if m.rb(a2 + 0x58):
            m.wb(a2 + 0x59, m.rb(a2 + 0x59) - 1)
            if m.rb(a2 + 0x59) == 0:
                if m.rb(a2 + 0x58) == 0x12:
                    points_285b0(m, a2, 9)                # Beginner's long pipe: 4000
                elif m.rb(a2 + 0x58) in (0x13, 0x14, 0x20, 0x22, 0x25):
                    points_285b0(m, a2, 5)                # the other pipes and the hole: 2000
                elif m.rb(a2 + 0x58) == 0x0a:
                    m.sound(0x45)
                m.wb(a2 + 0x58, 0); m.wl(a2 + 0x26, 0); m.wl(a2 + 0x22, 0); m.wb(a2 + 0x1a, 0)
    elif mode == 4:
        screen_pos_1b9cc(m, a2, True)
        done = reappear_13ade(m, a2)
        if not done and s8(m.rb(a2 + 0x57)) >= 2:
            return
        if occupied_17cb8(m, a2, s16(m.rw(a2 + 0x0c)), s16(m.rw(a2 + 0x10)), 0x70):
            m.wb(a2 + 0x57, 0x65)
            state_entry_25bae(m, a2, 4)               # somebody is on the spot: elsewhere
            done = False
        if done:
            m.wl(a2 + 4, 0); m.wl(a2, 0); m.wb(a2 + 0x1a, 0)
    elif mode == 6:                                    # over the line, rolling on (0x254D2):
        if m.rw(0x400390) == 1:                        # it keeps rolling even once finished
            demo_steer_1815a(m, a2)                    # (the demo steers it on, ROM 0x254DE)
        physics_121b8(m, a2)
        m.wb(a2 + 0x57, m.rb(a2 + 0x57) - 1)
        if m.rb(a2 + 0x57) == 0 and m.rb(a2 + 0x18) != 2:
            m.wb(a2 + 0x18, 3); m.wl(a2 + 4, 0); m.wl(a2, 0)
    elif mode == 7:
        helper_253bc(m, a2)
        m.wb(a2 + 0x1c, 0)
    elif mode == 8:
        if m.rb(a2 + 0x56):
            m.wb(a2 + 0x56, m.rb(a2 + 0x56) - 1)
        if m.rb(a2 + 0x56) == 0:
            m.wb(a2 + 0x56, 9); m.ww(a2 + 0x6a, m.rw(a2 + 0x6a) + 1)
            m.wb(a2 + 0x57, m.rb(a2 + 0x57) - 1)
        if m.rb(a2 + 0x57):
            m.wb(a2 + 0xd0, m.rb(a2 + 0xd0) + 1)
            if m.rb(a2 + 0xd0) == 2:
                m.wb(a2 + 0xd0, 0); m.wl(a2 + 0xcc, m.rl(a2 + 0xcc) + 4)
        else:
            m.wb(a2 + 0xd1, 0); m.wb(a2 + 0x1a, 0)
            points_285b0(m, a2, 0x10)
        screen_pos_1b9cc(m, a2, True)
        stripe_1c014(m, a2)
        sprite_list_1281c(m, a2)
    elif mode == 11:
        screen_pos_1b9cc(m, a2, True)
        if reappear_13ade(m, a2):
            m.wb(a2 + 0x57, 0x65)
            state_entry_25bae(m, a2, 4)
    elif mode == 9:                                    # held by a slinky (0x401482 object)
        slinky_pull_176d2(m, a2)
        anim_25fc2(m, a2)
        screen_pos_1b9cc(m, a2, True)
        sprite_list_1281c(m, a2)
    elif mode == 10:                                   # Aerial: sucked into a vacuum
        screen_pos_1b9cc(m, a2, True)
        if vacuum_pieces_13d38(m, a2):
            o = LEVEL_SLOTS[s8(m.rb(a2 + 0x58))]
            m.wl(o + 0x36, 0x1d752 if m.rb(o + 0x1f) == 0x0b else 0x1d798)
            m.sound(m.read32(0x1ef5a + 4 * (s8(m.rb(o + 0x1b)) - 30)))   # vacuum hum off
            level_object_program_12896(m, o)
            m.sound(0x3c)
            m.wl(a2 + 0x5a, 0); m.wb(a2 + 0x1a, 4)
            m.ww(a2 + 0xd2, m.rw(a2 + 0xd2) + 1)
            m.wb(a2 + 0x57, 0x65)
    else:
        m.unported.add(f'FUN_253EC mode {mode}')
        m.wb(a2 + 0x1a, 0)


def frame_sprites_1a8d2(m, a0, d1, d2, d3):
    """FUN_1A8D2: the motion objects of one animation frame.  a0 = frame (dx, dy,
    -, -, -, -, long sprite-data pointer with bit 0 = mirrored), (d1, d2) = screen
    position in sprite units (X + 24, Y + 16), d3 = bits or-ed into every code.
    Returns [(code, X word, Y word)] as written to sprite RAM."""
    out = []
    if a0 == 0xffffffff or not a0:
        return out
    d1 = s16(d1 + s8(m.romb(a0))); d2 = s16(d2 + s8(m.romb(a0 + 1)))
    p = m.read32(a0 + 8)
    flip = p & 1; p &= ~1
    d4, d5 = (0x8000, 0xff00) if flip else (0, 0x100)
    d6 = m.romb(p); p += 1
    if d6 != 0xff:                                   # a row of columns, one height and Y
        d1 = s16(d1 + s8(m.romb(p))); p += 1
        x = (d1 << 5) & 0x3fe0
        d4 |= m.romb(p); p += 1
        d2 = s16(d2 + s8(m.romb(p))); p += 1
        y = ((d2 << 5) & 0x3fe0) | d4
        for _ in range(d6 or 256):
            out.append(((m.read16(p) | d3) & 0xffff, x, y)); p += 2
            x = (x + d5) & 0xffff
    else:                                            # a row of columns, each its own height/Y
        p += 1
        d6 = m.romb(p); p += 1
        d1 = s16(d1 + s8(m.romb(p))); p += 1
        x = (d1 << 5) & 0x3fe0
        for _ in range(d6 or 256):
            d4 = (d4 & 0x8000) | m.romb(p); p += 1
            y = (((s16(s8(m.romb(p)) + d2)) << 5) & 0x3fe0) | d4; p += 1
            out.append(((m.read16(p) | d3) & 0xffff, x, y)); p += 2
            x = (x + d5) & 0xffff
    return out


# ---------------------------------------------------------------------------
# The hopping hazards at 0x401302 (4 slots, 0x60 apart): Beginner's green
# "acid" blobs.  They hop from cell to cell along a looping path, jump at a
# marble that rolls past at speed, and swallow it if they land close enough.
#   +0x00/+0x04 velocity, +0x0C/+0x10/+0x14 position, +0x18 active, +0x1A state,
#   +0x1B zone, +0x1C/+0x20 the push they got (state 2), +0x24/+0x25 frame
#   counter / frame length, +0x26 frame step (+1/-1), +0x27 jump time,
#   +0x28 screen position, +0x2C wall masks, +0x4A next path node, +0x4E path,
#   +0x52/+0x54 the scroll rows they live in, +0x56 their marble, +0x58 frame
#   pointer, +0x5C first frame of the current animation.
# States: 0 hopping along the path, 1 jumping at a marble, 2 knocked by a
# marble, 3 back to the path, 4 landing after a knock, 5 holding a marble,
# 6 missed.
# ---------------------------------------------------------------------------
HAZARD_SLOTS = [0x401302 + 0x60 * i for i in range(4)]
HOP_IDLE = 0x20c18


def object_pos_1bb08(m, a):
    m.ww(0x400690, m.rw(a + 0x0c)); m.ww(0x400692, m.rw(a + 0x10))
    derive_sprite_fields(m)


def _floor_at(m, a):
    object_pos_1bb08(m, a)
    return s32(sprite_project_1cc62(m, 1))


def hazard_screen_150d0(m, a):
    """FUN_150D0: screen position (+0x28: X << 16 | Y) and the wall masks."""
    x, y = s16(m.rw(a + 0x0c)), s16(m.rw(a + 0x10))
    m.ww(0x400690, x & 0xffff); m.ww(0x400692, y & 0xffff)
    sx = s16(y - x + 0x88)
    sy = s16(s16(m.rw(0x40097e)) + s16(m.rw(a + 0x14)) + 0x54 - ((x + y) >> 1))
    m.wl(a + 0x28, ((sx & 0xffff) << 16) | (sy & 0xffff))
    cover_264aa(m, a, 2)


def hazard_anim_15460(m, a):
    """FUN_15460: pick the animation for the hazard's state (+ its hop direction)."""
    mode = s8(m.rb(a + 0x1a))
    if mode in (0, 3):
        d3 = d2 = 0
        d4, d5 = s32(m.rl(a + 0x0c)) >> 19, s32(m.rl(a + 0x10)) >> 19
        node = m.rl(a + 0x4a)
        nx, ny = s8(m.romb(node)), s8(m.romb(node + 1))
        if m.rl(a):                                   # moving along X: keep to it
            order = ('y', 'x')
        else:
            order = ('x', 'y')
        for axis in order:
            if axis == 'x':
                if d4 > nx: d3 = -8; break
                if d4 < nx: d3 = 8; break
            else:
                if d5 > ny: d2 = -8; break
                if d5 < ny: d2 = 8; break
        if d2 > 0: frames = 0x20c6c
        elif d2 < 0: frames = 0x20c90
        elif d3 > 0: frames = 0x20c48
        elif d3 < 0: frames = 0x20cb4
        else: frames = HOP_IDLE
        m.wl(a + 0x5c, frames)
        m.wl(a, (d3 << 16) & 0xffffffff); m.wl(a + 4, (d2 << 16) & 0xffffffff)
        m.wb(a + 0x26, 1)
    elif mode == 2:
        if m.rl(a + 0x5c) != 0x20d64:
            if m.rl(a + 0x5c) == m.rl(a + 0x58):
                m.wl(a + 0x5c, 0x20d64); m.wb(a + 0x26, 1)
            else:
                d1 = s16((m.rl(a + 0x58) - m.rl(a + 0x5c)) >> 2)
                m.wb(a + 0x26, 1 if d1 >= 4 else 0xff)
    elif mode == 4:
        vx, vy = s32(m.rl(a + 0x1c)), s32(m.rl(a + 0x20))
        if abs(vx) > abs(vy):
            m.wl(a + 0x5c, 0x20dd8 if vx > 0 else 0x20e14)
        else:
            m.wl(a + 0x5c, 0x20dec if vy > 0 else 0x20e00)
        m.wb(a + 0x26, 1)
    elif mode == 1:
        m.wl(a + 0x5c, 0x20cd8); m.wb(a + 0x27, 0); m.wb(a + 0x26, 1)
    elif mode == 5:
        m.wl(a + 0x5c, 0x20e28)
    elif mode == 6:
        m.wl(a + 0x5c, 0x20d6c)
    else:
        return
    m.wl(a + 0x58, m.rl(a + 0x5c)); m.wb(a + 0x24, 0)
    m.wb(a + 0x25, 2 if mode in (0, 4) else 1)


def _octant(dx, dy):
    """The ROM's distance estimate: max + 3/8 min (unsigned words)."""
    dx &= 0xffff; dy &= 0xffff
    return ((dy >> 3) * 3 + dx) if dx > dy else ((dx >> 3) * 3 + dy)


def hazard_look_15670(m, a):
    """FUN_15670: a hopping hazard looks for a marble rolling past fast (|vx|+|vy| >
    0xC000) in its zone that no other hazard is already after; at a distance
    between 0x180 and 0x280 (1/16 px) it jumps at it (state 1)."""
    players = s16(m.rw(0x400396))
    d2 = s8(m.rb(0x400397))
    a1 = 0
    for i in range(players):
        a0 = 0x400018 + 0xe2 * i
        sp = abs(s32(m.rl(a0))) + abs(s32(m.rl(a0 + 4)))
        if (m.rb(a0 + 0x18) == 1 and m.rb(a + 0x1b) == m.rb(a0 + 0x1b) and sp > 0xc000
                and m.rb(a0 + 0x36) != 2 and s8(m.rb(a0 + 0x1a)) in (0, 1, 5)):
            taken = any(o != a and m.rb(o + 0x18) == 1 and m.rb(o + 0x1a) == 1
                        and s16(m.rw(o + 0x56)) == s8(m.rb(a0 + 0x19)) for o in HAZARD_SLOTS)
            if not taken:
                d2 -= 1; a1 = a0
    if d2 == players:
        return
    if players == 2 and d2 == 0:
        a1 = 0x4000fa if p2_ahead_15fe6(m, 0x4000fa, 0x400018) else 0x400018
    idx = s8(m.rb(a1 + 0x19))
    dx = (abs(s32(m.rl(a1 + 0x0c) - m.rl(a + 0x0c))) >> 12) & 0xffff
    dy = (abs(s32(m.rl(a1 + 0x10) - m.rl(a + 0x10))) >> 12) & 0xffff
    d1 = _octant(dx, dy)
    m.ww(a + 0x56, idx & 0xffff)
    if 0x180 < d1 < 0x280:
        m.wb(a + 0x1a, 1)
        hazard_anim_15460(m, a)


def hazard_nearest_node_14dec(m, a):
    """FUN_14DEC: back on the path at its node nearest to the hazard."""
    cx, cy = s32(m.rl(a + 0x0c)) >> 19, s32(m.rl(a + 0x10)) >> 19
    p = m.rl(a + 0x4e); best = 0x400; pick = 0
    while m.romb(p) != 0xff and m.romb(p + 1) != 0xff:
        dx = (abs(s16(cx - s8(m.romb(p)))) << 4) & 0xffff
        dy = (abs(s16(cy - s8(m.romb(p + 1)))) << 4) & 0xffff
        d = _octant(dx, dy) & 0xffff
        if d < best:
            best = d; pick = p
        p += 4
    m.wl(a + 0x4a, pick)


def friction_25e7c(m, a, kind=2):
    """FUN_25E7C, kind 2 (a knocked hazard sliding): speed loses 4x the table
    value 0x1EEF8 (by speed) each update; velocity scaled by new/old speed."""
    vx, vy = s32(m.rl(a)), s32(m.rl(a + 4))
    d2, d4 = abs(vx) & 0xffffffff, abs(vy) & 0xffffffff
    if d2 > d4:
        d3 = ((d4 >> 3) & 0xffff) * 3 + d2
    else:
        d3 = ((d2 >> 3) & 0xffff) * 3 + d4
    d3 &= 0xffffffff
    i = (d3 >> 15) & 15
    frac = (d3 >> 12) & 7
    t0 = s16(m.romw(0x1eef8 + 2 * i)); t1 = s16(m.romw(0x1eef8 + 2 * i + 2))
    d2 = (s32(s16(t1 - t0) * frac) >> 3) + t0
    if d3 < 0x100:
        d3 = 0x100
    if kind != 2:
        m.unported.add(f'FUN_25E7C kind {kind}')
    d2 = (d2 << 2) & 0xffffffff
    d0 = (d3 - d2) & 0xffffffff if d2 < d3 else 0
    num = (d0 << 6) & 0xffffffff
    den = (d3 >> 8) & 0xffff
    q = num // den
    d5 = q & 0xffff if q <= 0xffff else num & 0xffff        # divu overflow: unchanged
    f = s16(d5)
    m.wl(a, (s32((vx >> 8) * f) >> 6) & 0xffffffff)
    m.wl(a + 4, (s32((vy >> 8) * f) >> 6) & 0xffffffff)


def hazard_state_15148(m, a):
    """FUN_15148: the hazard's state machine, run when an animation ends (state 2:
    every update)."""
    mode = s8(m.rb(a + 0x1a))
    if mode in (0, 3):
        cx, cy = s32(m.rl(a + 0x0c)) >> 19, s32(m.rl(a + 0x10)) >> 19
        start, node = m.rl(a + 0x4e), m.rl(a + 0x4a)
        if (s8(m.romb(start)) == cx and s8(m.romb(start + 1)) == cy
                and m.rl(a + 0x5c) != HOP_IDLE):            # round the loop: rest a moment
            if mode == 3:
                m.wb(a + 0x1a, 0)
            m.wl(a + 0x5c, HOP_IDLE); m.wl(a + 4, 0); m.wl(a, 0)
            m.wb(a + 0x26, 1); m.wl(a + 0x58, HOP_IDLE); m.wb(a + 0x24, 0); m.wb(a + 0x25, 2)
            m.wl(a + 0x4a, (start + 4 * s8(m.romb(node + 2))) & 0xffffffff)
        elif s8(m.romb(node)) == cx and s8(m.romb(node + 1)) == cy:
            if mode == 3:
                m.wb(a + 0x1a, 0)
            m.wl(a + 0x4a, (start + 4 * s8(m.romb(node + 2))) & 0xffffffff)
            hazard_anim_15460(m, a)
        else:
            hazard_anim_15460(m, a)
        if s8(m.rb(a + 0x1a)) == 0:
            hazard_look_15670(m, a)
    elif mode == 2:                                     # knocked: slide until stopped
        friction_25e7c(m, a, 2)
        if m.rl(a) == 0 and m.rl(a + 4) == 0:
            m.wb(a + 0x1a, 4)
            hazard_anim_15460(m, a)
            return
        x, y, z = m.rl(a + 0x0c), m.rl(a + 0x10), s32(m.rl(a + 0x14))
        m.wl(a + 0x0c, (x + m.rl(a)) & 0xffffffff); m.wl(a + 0x10, (y + m.rl(a + 4)) & 0xffffffff)
        nz = _floor_at(m, a)
        m.wl(a + 0x14, nz & 0xffffffff)
        if z != nz:                                     # off the edge of its level: stop
            m.wb(a + 0x1a, 4 if z > nz else 1)
            m.wl(a + 0x0c, x); m.wl(a + 0x10, y); m.wl(a + 0x14, z & 0xffffffff)
        hazard_anim_15460(m, a)
    elif mode == 4:
        m.wb(a + 0x1a, 1)
        hazard_anim_15460(m, a)
    elif mode == 1:                                     # landing from the jump
        t = m.read32(OBJ_TABLE + 4 * s16(m.rw(a + 0x56)))
        d2 = s32(m.rl(t + 0x0c) - m.rl(a + 0x0c)); d3 = s32(m.rl(t + 0x10) - m.rl(a + 0x10))
        d4 = s16(abs(d2) >> 12); d0 = s16(abs(d3) >> 12)
        caught = False
        if d4 <= 0x100 and d0 <= 0x100:
            d1 = s16((d0 >> 3) * 3 + d4) if d4 > d0 else s16((d4 >> 3) * 3 + d0)
            if d1 < 0x70:                               # close enough: swallowed
                m.wl(t + 0x0c, m.rl(a + 0x0c)); m.wl(t + 0x10, m.rl(a + 0x10))
                m.wb(t + 0x1a, 7); m.wb(a + 0x1a, 5)
                m.sound(0x33)                           # swallowed
                caught = True
            elif d1 < 0xc0:                             # near: sucked towards it
                qx = _divs(d2, d1); qy = _divs(d3, d1)
                m.wl(t, (s32(0x400 * s16(qx)) >> 4) & 0xffffffff)
                m.wl(t + 4, (s32(0x400 * s16(qy)) >> 4) & 0xffffffff)
        if not caught:
            m.wb(a + 0x1a, 6)
        hazard_anim_15460(m, a)
    elif mode in (5, 6):                                # done: snap to the cell, back to the path
        m.wl(a + 0x0c, ((m.rl(a + 0x0c) & 0xfff80000) + 0x40000) & 0xffffffff)
        m.wl(a + 0x10, ((m.rl(a + 0x10) & 0xfff80000) + 0x40000) & 0xffffffff)
        if mode == 5:                                   # the marble it held is lost
            t = m.read32(OBJ_TABLE + 4 * s16(m.rw(a + 0x56)))
            stop_roll_15884(m)                          # (0x1542C)
            m.wb(t + 0x57, 0x65)
            state_entry_25bae(m, t, 4)
        m.wb(a + 0x1a, 3)
        hazard_nearest_node_14dec(m, a)
        hazard_anim_15460(m, a)


def hazard_update_14966(m, a):
    """FUN_14966: one hazard, one update (animation, hop/jump movement, screen)."""
    if not m.rb(a + 0x18):
        return
    m.wb(a + 0x24, m.rb(a + 0x24) + 1)
    if s8(m.rb(a + 0x25)) > s8(m.rb(a + 0x24)):
        if s8(m.rb(a + 0x1a)) == 2:
            hazard_state_15148(m, a)
    else:
        m.wb(a + 0x24, 0)
        m.wl(a + 0x58, (m.rl(a + 0x58) + 4 * s8(m.rb(a + 0x26))) & 0xffffffff)
        if m.read32(m.rl(a + 0x58)) == 0xffffffff or m.rl(a + 0x5c) == m.rl(a + 0x58):
            m.wl(a + 0x58, m.rl(a + 0x5c))
            if s8(m.rb(a + 0x26)) > 0 and s8(m.rb(a + 0x1a)) in (0, 3):   # a hop done: one cell on
                m.wl(a + 0x0c, (m.rl(a + 0x0c) + m.rl(a)) & 0xffffffff)
                m.wl(a + 0x10, (m.rl(a + 0x10) + m.rl(a + 4)) & 0xffffffff)
            hazard_state_15148(m, a)
        elif s8(m.rb(a + 0x1a)) == 2:
            hazard_state_15148(m, a)
    if s8(m.rb(a + 0x1a)) in (1, 5, 6):
        t27 = s8(m.rb(a + 0x27))
        if 5 < t27 < 0x26:                              # in the air: the jump arc
            d5 = _floor_at(m, a)
            m.wl(a + 0x14, (s32(s8(m.romb(0x2255a + t27 - 6)) << 16) + d5) & 0xffffffff)
            t = m.read32(OBJ_TABLE + 4 * s16(m.rw(a + 0x56)))
            if (m.rb(t + 0x18) == 1 and m.rb(a + 0x1b) == m.rb(t + 0x1b) and not m.rb(t + 0x36)
                    and s32(m.rl(t + 0x14)) == d5 and s8(m.rb(t + 0x1a)) in (0, 1, 5, 7)):
                d2 = s32(m.rl(t + 0x0c) - m.rl(a + 0x0c)); d3 = s32(m.rl(t + 0x10) - m.rl(a + 0x10))
                m.wl(a, (d2 >> 4) & 0xffffffff); m.wl(a + 4, (d3 >> 4) & 0xffffffff)
                d4 = (abs(d2) >> 12) & 0xffff; d0 = (abs(d3) >> 12) & 0xffff
                d1 = _octant(d4, d0) & 0xffff
                if d1:
                    q2 = s16(_divs(d2, d1)); q1 = s16(_divs(d3, d1))
                else:
                    q2 = q1 = 0
                g2 = s32(0x800 * q2) >> 4; g3 = s32(0x800 * q1) >> 4
                vx, vy = s32(m.rl(a)), s32(m.rl(a + 4))
                m.wl(a, (vx + (s32(g2 - vx) >> 5)) & 0xffffffff)
                m.wl(a + 4, (vy + (s32(g3 - vy) >> 5)) & 0xffffffff)
            else:
                m.wl(a, 0); m.wl(a + 4, 0)
            m.wl(0x400684, m.rl(a + 0x0c)); m.wl(0x400688, m.rl(a + 0x10))
            m.wl(a + 0x0c, (m.rl(a + 0x0c) + m.rl(a)) & 0xffffffff)
            m.wl(a + 0x10, (m.rl(a + 0x10) + m.rl(a + 4)) & 0xffffffff)
            if _floor_at(m, a) != d5:                   # would leave its floor: stay
                m.wl(a + 0x0c, m.rl(0x400684)); m.wl(a + 0x10, m.rl(0x400688))
        m.wb(a + 0x27, m.rb(a + 0x27) + 1)
    hazard_screen_150d0(m, a)


def hazards_update_1493c(m):
    for a in HAZARD_SLOTS:
        hazard_update_14966(m, a)


def hazards_spawn_14c46(m, o, n):
    """FUN_14C46 (from the scroll): start the hazards whose rows come into view
    (table 0x2257A per race: first row, last row, path, zone) and end those whose
    rows have gone."""
    p = m.read32(0x2257a + 4 * m.rw(0x400394))
    o, n = s8(o), s8(n)
    while m.romb(p) != 0xff:
        lo, hi = s8(m.romb(p)), s8(m.romb(p + 1))
        slot = 0xffffffff
        for k in range(4):                              # FUN_14BCE: the last free slot
            s_ = m.read32(0x1f006 + 4 * k)
            if not m.rb(s_ + 0x18):
                slot = s_
        if slot == 0xffffffff:
            break
        path = m.read32(p + 2)
        used = any(m.rb(h + 0x18) and m.rl(h + 0x4e) == path for h in HAZARD_SLOTS)   # FUN_14C0C
        if not used and n in (lo, hi) and (o < lo or o > hi):
            a = slot
            m.wl(a + 0x4a, path); m.wl(a + 0x4e, path)
            m.wb(a + 0x1b, m.romb(p + 6))
            m.ww(a + 0x52, lo & 0xffff); m.ww(a + 0x54, hi & 0xffff)
            m.wl(a + 0x0c, ((s8(m.romb(path)) << 19) + 0x40000) & 0xffffffff)
            m.wl(a + 0x10, ((s8(m.romb(path + 1)) << 19) + 0x40000) & 0xffffffff)
            m.wl(a + 0x14, _floor_at(m, a) & 0xffffffff)
            m.wl(a + 4, 0); m.wl(a, 0)
            m.wb(a + 0x18, 1); m.wb(a + 0x1a, 0); m.wl(a + 0x28, 0)
            for k in range(5):
                m.ww(a + 0x2c + 6 * k, 0)
            m.wl(a + 0x5c, HOP_IDLE); m.wl(a + 0x58, HOP_IDLE)
            m.wb(a + 0x26, 1); m.wb(a + 0x24, 0); m.wb(a + 0x25, 2)
            hazard_screen_150d0(m, a)
        p += 8
    for a in HAZARD_SLOTS:
        if m.rb(a + 0x18):
            lo, hi = s16(m.rw(a + 0x52)), s16(m.rw(a + 0x54))
            if (o == lo and n < lo) or (o == hi and n > hi):
                m.wb(a + 0x18, 0)


def hazard_contact_14e92(m, a2):
    """FUN_14E92 (Beginner, Intermediate, Ultimate): the marble against the hopping
    hazards.  Box test against the current frame's box; a hopping hazard is knocked
    away (state 2); the marble stops (back to its previous position) and is dazed
    (state 5 for 50 updates).  Returns True on a hit."""
    if m.rw(0x400394) not in (1, 2, 5):
        return False
    d6 = s16(m.rw(0x400690) - 3); a4 = s16(d6 + 6)
    a1 = s16(m.rw(0x400692) - 3); y2 = s16(a1 + 6)
    z6 = s16(m.rw(0x400694)); z4 = s16(z6 + 14)
    hit = False
    for a3 in HAZARD_SLOTS:
        if not m.rb(a3 + 0x18):
            continue
        fr = m.read32(m.rl(a3 + 0x58))
        if fr == 0xffffffff:
            d0, d3, d2, d4 = -4, -4, 8, 8
        else:
            d0, d3, d2, d4 = (s8(m.romb(fr + k)) for k in (4, 5, 6, 7))
        x0 = s16(s16(m.rw(a3 + 0x0c)) + d0); x1 = s16(d2 + x0)
        y0 = s16(s16(m.rw(a3 + 0x10)) + d3); y1 = s16(d4 + y0)
        zz0 = s16(m.rw(a3 + 0x14)); zz1 = s16(zz0 + 16)
        if not ((zz0 <= z6 <= zz1) or (zz0 <= z4 <= zz1)):
            continue
        if not ((x0 <= d6 <= x1) or (x0 <= a4 <= x1)):
            continue
        if not ((y0 <= a1 <= y1) or (y0 <= y2 <= y1)):
            continue
        hit = True
        hm = s8(m.rb(a3 + 0x1a))
        if hm in (0, 3):
            m.wb(a3 + 0x1a, 2)
            hazard_anim_15460(m, a3)
            vx, vy = m.rl(a2), m.rl(a2 + 4)
            m.wl(a3, vx); m.wl(a3 + 0x1c, vx); m.wl(a3 + 4, vy); m.wl(a3 + 0x20, vy)
        hm = s8(m.rb(a3 + 0x1a))
        if hm in (1, 5, 6) and s8(m.rb(a2 + 0x19)) == s16(m.rw(a3 + 0x56)) or hm not in (1, 5, 6):
            if hm in (1, 6):
                return True
        pm = s8(m.rb(a2 + 0x1a))
        if pm != 5 and hm != 5:
            m.ww(a3 + 0x56, s8(m.rb(a2 + 0x19)) & 0xffff)
        m.wl(a2 + 0x0c, m.rl(0x400684)); m.wl(a2 + 0x10, m.rl(0x400688))
        m.wl(a2 + 4, 0); m.wl(a2, 0)
        if pm == 1:
            m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x60, 2); m.wl(a2 + 0x5a, 0x20fb6)
        elif pm != 5:
            m.sound(0x39)
            m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x60, 2); m.wl(a2 + 0x5a, 0x20faa)
        if pm not in (5, 7):
            m.wb(a2 + 0x1a, 5); m.wb(a2 + 0x56, 0x32)
        return True
    return hit


def occlusion_map(m):
    """Debug view of FUN_264AA's data for the whole screen, independent of the marble:
    for every 8-pixel column, every occluder row in its bitmap gets the slope mask the
    routine would use there (from the terrain top edges, FUN_177F8), and every entry of
    the hand-placed table for this race its own mask.  Returns [(code, cx, cy, source)]
    in the routine's own coordinates (cx = column * 8, cy = sprite Y)."""
    hdr = m.rl(0x400474)
    rows = s16(m.read16(hdr + 24))
    scroll = s16(m.rw(0x40097e))
    race = m.rw(0x400394)
    saved = m.rl(0x400988)
    out = []
    for col in range(36):
        colptr = (s16(m.rw(0x40045c)) + hdr + 46 + col * 4) & 0xffffffff
        base = m.read32(colptr)
        for row in range(max(0, rows - 1)):
            if not (m.read8(base + (row >> 3)) >> (row & 7)) & 1:
                continue
            m.wl(0x400988, (col ^ row) & 1)
            ref = s16(row * 4 - scroll)
            top0 = s16(terrain_top_177f8(m, row, col, 0) - ref)
            if not -64 < top0 < 320:
                continue
            m.wl(0x400988, m.rl(0x400988) ^ 1)
            top1 = s16(terrain_top_177f8(m, row + 1, col, 2) - (ref + 4))
            if m.rl(0x400988) == 0:
                top0, top1 = top1, top0
            k = abs(top1 - top0)
            if k > 16:
                continue                                  # (past the slope table: not drawn)
            if top0 <= top1:
                out.append((m.romw(0x1ead2 + 2 * k), col * 8, s16(s16(m.romw(0x1eaf4 + 2 * k)) + top0), 'terrain'))
            else:
                out.append(((m.romw(0x1ead2 + 2 * k) + 0x8000) & 0xffff, col * 8,
                            s16(s16(m.romw(0x1eaf4 + 2 * k)) + top1), 'terrain'))
        a1 = m.read32(0x1ea2a + 4 * col)
        off = s16(m.read16(a1 + 2 * race))
        if off:
            p = a1 + 2 * off
            while s16(m.read16(p)) != -1:
                kind = s16(m.read16(p + 4))
                if kind >= 0 or kind == -1:
                    yy = s16(s16(m.read16(p + 6)) + scroll - s16(m.read16(hdr + 16)))
                    code = m.read16(p + 8)
                    if code and -64 < yy < 320:
                        out.append((code, col * 8, yy, 'table'))
                p += 10
    m.wl(0x400988, saved)
    return out


def random_13a98(m, n):
    """FUN_13A98: the game's random numbers - a 16-bit shift register at 0x4003A6,
    clocked once per bit of n; returns a number below n (n = 0: the raw register)."""
    d0 = m.rw(0x4003a6)
    d3 = 0xffff0000
    d1 = n & 0xffff
    while True:
        d2 = ((d0 >> 8) ^ d0) & 0xff
        if d2 == 0:
            d2 = 0x40
        x = (d2 >> 6) & 1                              # asl.b #2: the last bit shifted out
        d0 = ((d0 << 1) | x) & 0xffff                  # roxl.w #1
        d3 = ((d3 << 1) | (d3 >> 31)) & 0xffffffff     # rol.l #1
        d1 >>= 1
        if not d1:
            break
    m.ww(0x4003a6, d0)
    if n == 0:
        return d0
    d0 &= d3 & 0xffff
    while not s16(n) > s16(d0):
        d0 = (d0 - n) & 0xffff
    return d0


# ---------------------------------------------------------------------------
# The 0x401482 objects (7 slots, 0x42 apart): Intermediate's slinkies.  They walk
# from cell to cell along a looping path; touching one, the marble is held (state
# 9), pulled to the slinky's middle, and lost when the hold animation ends.
#   +0x00/+0x04 velocity, +0x0C/+0x10/+0x14 position, +0x18 active, +0x1B zone,
#   +0x24/+0x25 frame counter / frame length, +0x28 screen position, +0x2C next
#   path node, +0x30 path, +0x34/+0x36 scroll rows, +0x3A frame, +0x3E first frame.
# ---------------------------------------------------------------------------
SLINKY_SLOTS = [0x401482 + 0x42 * i for i in range(7)]


def slinky_screen_1778e(m, a):
    x, y = s16(m.rw(a + 0x0c)), s16(m.rw(a + 0x10))
    m.ww(0x400690, x & 0xffff); m.ww(0x400692, y & 0xffff)
    sx = s16(y - x + 0x88)
    sy = s16(s16(m.rw(0x40097e)) + s16(m.rw(a + 0x14)) + 0x54 - ((x + y) >> 1))
    m.wl(a + 0x28, ((sx & 0xffff) << 16) | (sy & 0xffff))


def slinky_step_1d242(m, a):
    """FUN_1D242: one cell towards the next node, animation by direction; walks
    faster (+0x25 = 1) while a marble is in zone 6 with it."""
    d4, d5 = s32(m.rl(a + 0x0c)) >> 19, s32(m.rl(a + 0x10)) >> 19
    node = m.rl(a + 0x2c)
    nx, ny = s8(m.romb(node)), s8(m.romb(node + 1))
    d3 = d2 = 0
    for axis in (('y', 'x') if m.rl(a) else ('x', 'y')):
        if axis == 'x':
            if d4 > nx: d3 = -8; break
            if d4 < nx: d3 = 8; break
        else:
            if d5 > ny: d2 = -8; break
            if d5 < ny: d2 = 8; break
    if d2 > 0: f = 0x20ec4
    elif d2 < 0: f = 0x20ee4
    elif d3 > 0: f = 0x20ea4
    else: f = 0x20f24
    m.wl(a + 0x3e, f)
    m.wl(a, (d3 << 16) & 0xffffffff); m.wl(a + 4, (d2 << 16) & 0xffffffff)
    m.wl(a + 0x3a, f); m.wb(a + 0x24, 0); m.wb(a + 0x25, 2)
    for i in range(s16(m.rw(0x400396))):
        p = 0x400018 + 0xe2 * i
        if m.rb(p + 0x18) == 1 and m.rb(a + 0x1b) == m.rb(p + 0x1b) and m.rb(p + 0x1b) == 6:
            m.wb(a + 0x25, 1)
            break


def slinky_next_1d1ec(m, a):
    cx, cy = s32(m.rl(a + 0x0c)) >> 19, s32(m.rl(a + 0x10)) >> 19
    node = m.rl(a + 0x2c)
    if s8(m.romb(node)) == cx and s8(m.romb(node + 1)) == cy:
        m.wl(a + 0x2c, (m.rl(a + 0x30) + 4 * s8(m.romb(node + 2))) & 0xffffffff)
    slinky_step_1d242(m, a)


def slinky_update_1725a(m, a):
    if not m.rb(a + 0x18):
        return
    m.wb(a + 0x24, m.rb(a + 0x24) + 1)
    if not s8(m.rb(a + 0x25)) > s8(m.rb(a + 0x24)):
        m.wb(a + 0x24, 0)
        if s8(m.rb(a + 0x25)) > 2:                     # (after holding a marble: back to speed)
            m.wb(a + 0x25, 1)
        m.wl(a + 0x3a, (m.rl(a + 0x3a) + 4) & 0xffffffff)
        if m.read32(m.rl(a + 0x3a)) == 0xffffffff:      # a step done: one cell on
            m.wl(a + 0x3a, m.rl(a + 0x3e))
            m.wl(a + 0x0c, (m.rl(a + 0x0c) + m.rl(a)) & 0xffffffff)
            m.wl(a + 0x10, (m.rl(a + 0x10) + m.rl(a + 4)) & 0xffffffff)
            slinky_next_1d1ec(m, a)
    slinky_screen_1778e(m, a)


def slinkies_update_17230(m):
    for a in SLINKY_SLOTS:
        slinky_update_1725a(m, a)


def _slinky_start(m, a, path, ent):
    m.wl(a + 0x2c, path); m.wl(a + 0x30, path)
    m.wb(a + 0x1b, m.romb(ent + 6))
    m.ww(a + 0x34, s8(m.romb(ent)) & 0xffff); m.ww(a + 0x36, s8(m.romb(ent + 1)) & 0xffff)
    m.wl(a + 0x0c, (s8(m.romb(path)) << 19) & 0xffffffff)
    m.wl(a + 0x10, (s8(m.romb(path + 1)) << 19) & 0xffffffff)
    m.wl(a + 0x14, _floor_at(m, a) & 0xffffffff)
    m.wl(a + 4, 0); m.wl(a, 0)
    m.wb(a + 0x18, 1); m.wb(a + 0x1a, 0); m.wl(a + 0x28, 0)
    slinky_next_1d1ec(m, a)
    slinky_screen_1778e(m, a)


def slinkies_spawn_17346(m, o, n):
    """FUN_17346 (from the scroll): table 0x23D4A per race (first row, last row, path,
    zone).  A path of 0 starts a whole group (1 in zone 5, else 6), its paths from one
    of two lists picked at random."""
    p = m.read32(0x23d4a + 4 * m.rw(0x400394))
    o, n = s8(o), s8(n)

    def free_slot():                                   # FUN_172C2: the last free slot
        f = None
        for a in SLINKY_SLOTS:
            if not m.rb(a + 0x18):
                f = a
        return f

    stop = False
    while m.romb(p) != 0xff and not stop:
        lo, hi = s8(m.romb(p)), s8(m.romb(p + 1))
        path = m.read32(p + 2)
        used = any(m.rb(a + 0x18) and m.rl(a + 0x30) == path for a in SLINKY_SLOTS)   # FUN_1730C
        if not used and n in (lo, hi) and (o < lo or o > hi):
            if path == 0:
                zone5 = m.romb(p + 6) == 5
                count = 1 if zone5 else 6
                r = random_13a98(m, 2)
                lst = (0x23d62 if zone5 else 0x23d6a) if r == 0 else (0x23d66 if zone5 else 0x23d82)
                for k in range(count):
                    a = free_slot()
                    if a is None:
                        stop = True
                        break
                    _slinky_start(m, a, m.read32(lst + 4 * k), p)
            else:
                a = free_slot()
                if a is None:
                    break
                _slinky_start(m, a, path, p)
        p += 8
    for a in SLINKY_SLOTS:
        if m.rb(a + 0x18):
            lo, hi = s16(m.rw(a + 0x34)), s16(m.rw(a + 0x36))
            if (o == lo and n < lo) or (o == hi and n > hi):
                m.wb(a + 0x18, 0)


def slinky_contact_175c8(m, a2):
    """FUN_175C8 (Intermediate, Ultimate): the marble (a 6x6 box) touching a slinky's
    frame box is caught: state 9, +0x58 = which slinky; the slinky slows (+0x25 = 28)."""
    if m.rw(0x400394) not in (2, 5):
        return False
    d6 = s16(m.rw(0x400690) - 3); a4 = s16(d6 + 6)
    a1 = s16(m.rw(0x400692) - 3); y2 = s16(a1 + 6)
    for a3 in SLINKY_SLOTS:
        if not m.rb(a3 + 0x18):
            continue
        fr = m.read32(m.rl(a3 + 0x3a))
        if fr == 0xffffffff:
            b4, b5, b6, b7 = -2, -2, 12, 12
        else:
            b4, b5, b6, b7 = (s8(m.romb(fr + k)) for k in (4, 5, 6, 7))
        x0 = s16(s16(m.rw(a3 + 0x0c)) + b4); x1 = s16(b6 + x0)
        y0 = s16(s16(m.rw(a3 + 0x10)) + b5); y1 = s16(b7 + y0)
        if not ((x0 <= d6 <= x1) or (x0 <= a4 <= x1)):
            continue
        if not ((y0 <= a1 <= y1) or (y0 <= y2 <= y1)):
            continue
        state_entry_25bae(m, a2, 9)
        m.sound(0x5e)                                  # caught by a slinky
        m.wb(a2 + 0x58, m.rb(a3 + 0x19))
        m.wb(a3 + 0x25, 0x1c)
        return True
    return False


def slinky_pull_176d2(m, a1):
    """FUN_176D2: a held marble moves one pixel a tick (on each axis) towards the
    middle of its slinky's frame box."""
    a2 = SLINKY_SLOTS[s8(m.rb(a1 + 0x58))]
    fr = m.read32(m.rl(a2 + 0x3a))
    if fr == 0xffffffff:
        b4, b5, b6, b7 = -2, -2, 12, 12
    else:
        b4, b5, b6, b7 = (s8(m.romb(fr + k)) for k in (4, 5, 6, 7))
    cx = s16((b6 >> 1) + b4 + s16(m.rw(a2 + 0x0c)))
    cy = s16((b7 >> 1) + b5 + s16(m.rw(a2 + 0x10)))
    mx, my = s16(m.rw(a1 + 0x0c)), s16(m.rw(a1 + 0x10))
    dx = -1 if mx > cx else (1 if mx < cx else 0)
    dy = -1 if my > cy else (1 if my < cy else 0)
    m.wl(a1 + 0x0c, ((mx + dx) << 16) & 0xffffffff)
    m.wl(a1 + 0x10, ((my + dy) << 16) & 0xffffffff)


def terrain_cells_1d06a(m, d1):
    """FUN_1D06A (type-6 objects, Intermediate's moving floor): its step (+0x25)
    picks up to four groups of three dynamic cells (0x40076E) and writes their codes
    from the ROM tables at 0x24C98-0x24E1A."""
    if d1 <= 12:
        d1 = s8(m.romb(0x24e1a + d1))
    else:
        d1 -= 6
    a1 = 0x24c98 + 4 * ((((d1 - 7) & 3) + 7) & 0xffff)
    d1 >>= 2
    a2 = 0x40076e + 6 * d1
    a3 = 0x24c7a + d1
    for _ in range(4):
        c = s8(m.romb(a3))
        if c < 0:
            return
        k = 2 * s8(m.romb(a1))
        if c <= 3:
            third = (0x24d5c, 0x24da8, 0x24dce, 0x24df4)[c]
            codes = (0x24d10, 0x24cc4, third)
        else:
            codes = (0x24d36, 0x24cea, 0x24d82)
        for t in codes:
            m.ww(a2, m.romw(t + k)); a2 += 2
        if c > 3:
            return
        a1 += 1; a3 += 1


# ---------------------------------------------------------------------------
# Aerial (race 3): the cup that throws the marble (level object type 0x0A), the
# vacuums (0x0B facing one way, 0x0D the other), the bumpers (0x0C), two marker
# types (0x17/0x18), and the 36 pop-up blocks of zones 4/5 (0x401650, 16 bytes each).
# ---------------------------------------------------------------------------
_STALE = [0, 0]          # 29CCE's locals -18/-20: handler 0x0D reads them without setting them


def _toward(d6, a0):
    """The vacuums' and bumpers' direction: (d6, a0) scaled to length 0x1000 (16.16 /
    an octagonal distance), as the ROM works it out."""
    d1 = (abs(d6) << 4) & 0xffff
    d0 = (abs(a0) << 4) & 0xffff
    if d1 > d0:
        a1 = s16((d0 >> 3) * 3 + d1)
    else:
        a1 = s16((d1 >> 3) * 3 + d0)
    if a1 == 0:
        return None
    q = lambda v: s16(int((s16(v) << 16) / a1))     # divs: the quotient, toward zero
    return q(d6), q(a0)


def _aerial_contact(m, a2, o, t, d6, a0, vx0, vy0, FX, FY):
    d1 = s16((s16(m.rw(o + 0x0c)) >> 3) - s16(m.rw(0x400696)))
    d2 = s16((s16(m.rw(o + 0x10)) >> 3) - s16(m.rw(0x400698)))
    z, zprev = s16(m.rw(0x400694)), s16(m.rw(0x40068c))
    ox, oy = s16(m.rw(o + 0x0c)), s16(m.rw(o + 0x10))
    if t in (0x17, 0x18):                                  # markers along the start
        if (t == 0x17 and d1 == 0 and -18 < d2 <= 0) or (t == 0x18 and d2 == 0 and -18 < d1 <= 0):
            m.wb(a2 + 0x58, t); m.wb(a2 + 0x59, 0xff)
        return False
    if t == 0x0a:                                          # the cup (0x29E22)
        if not (-8 < d6 < 8 and -8 < a0 < 8) or m.rl(a2 + 8):
            return False
        if m.rl(o + 0x3e) == m.rl(o + 0x46) and m.rw(o + 0x1c) == 0:
            if m.rw(0x400396) == 2:
                other = 0x400018 + 0xe2 * (1 - s8(m.rb(a2 + 0x19)))
                if m.rb(other + 0x18) == 1 and m.rb(other + 0x58) == 0x0a:
                    m.wl(a2 + 0x0c, m.rl(0x400684)); m.wl(a2 + 0x10, m.rl(0x400688))
                    m.wl(a2 + 0x14, m.rl(0x40068c))          # (FUN_2648C)
                    m.wl(a2 + 4, 0); m.wl(a2, 0)
                    return False
            m.wl(a2 + 0x0c, m.rl(o + 0x0c)); m.wl(a2 + 0x10, m.rl(o + 0x10))
            m.wl(a2 + 0x14, (m.rl(a2 + 0x14) - 0x30000) & 0xffffffff)
            m.wl(a2 + 8, 0xa0000)
            m.wl(a2, (random_13a98(m, 0x2000) - 0x1000) & 0xffffffff)
            m.wl(a2 + 4, (-0x25000 - (random_13a98(m, 0xa000) & 0xffff)) & 0xffffffff)
            m.wb(a2 + 0x36, 2)
            stop_roll_15884(m)
            m.wb(a2 + 0x1a, 3); m.wb(a2 + 0x58, t); m.wb(a2 + 0x59, 0x0f)
            m.wl(o + 0x36, 0x1db80)
            level_object_program_12896(m, o)
        else:
            m.wl(a2 + 0x0c, m.rl(0x400684)); m.wl(a2 + 0x10, m.rl(0x400688))
        return False
    if t == 0x0c:                                          # a bumper (0x2A9A2)
        if not m.rb(o + 0x1a):
            return False
        fr = m.read32(m.rl(o + 0x3e))
        # (no check for the script's end marker 0xFFFFFFFF here: the ROM then reads
        #  the box from address 3 - the 68010's 24-bit bus wraps round)
        b4, b5, b6, b7 = (s8(m.read8((fr + k) & 0xffffff)) for k in (4, 5, 6, 7))
        L6, L8 = s16(-(b4 - 3)), s16(-(b5 - 3))
        lo_x, lo_y = s16(-(b6 + b4 + 3)), s16(-(b7 + b5 + 3))
        _STALE[0] = s16(ox - m.rw(0x400684)); _STALE[1] = s16(oy - m.rw(0x400688))
        if not (lo_x <= d6 < L6 and lo_y <= a0 < L8):
            return False
        if lo_x <= _STALE[0] < L6 and lo_y <= _STALE[1] < L8:
            d6, a0 = s16(d6 + 4), s16(a0 + 4)
            r = _toward(-d6, -a0)
            vx, vy = r if r else (0x1000, 0)
            m.wl(a2, (vx << 6) & 0xffffffff); m.wl(a2 + 4, (vy << 6) & 0xffffffff)
            if m.rb(a2 + 0x1a) != 1:
                m.wb(a2 + 0x5f, 0); m.wb(a2 + 0x60, 2); m.wl(a2 + 0x5a, 0x20faa)
                m.sound(0x39)
            m.wb(a2 + 0x1a, 1)
            landing_25c74(m, a2, 0)
            m.wb(a2 + 0x57, 0x3c); m.wb(a2 + 0x56, 0)
        else:
            m.wb(FX, 1); m.wb(FY, 1)
        return False
    # the vacuums: 0x0B (0x2AB88) and its mirror 0x0D (0x2AD20)
    if not m.rb(o + 0x1a):
        return False
    b = t == 0x0b
    if m.rl(o + 0x46) != (0x22016 if b else 0x220a6):
        return False
    if b:
        mouth = -12 <= d6 < 28 and -32 <= a0 < -8
        inside = 0 <= d6 < 16 and -15 <= a0 < -9
    else:
        mouth = -32 <= d6 < -8 and -12 <= a0 < 28
        inside = -15 <= d6 < -9 and 0 <= a0 < 16
    if mouth:
        if inside:
            m.wb(a2 + 0x1a, 0x0a); m.wb(a2 + 0x57, 0x20); m.wb(a2 + 0x58, m.rb(o + 0x19))
            return False
        if b:
            a0, d6 = s16(a0 + 12), s16(d6 - 8)
        else:
            d6, a0 = s16(d6 + 12), s16(a0 - 8)
        r = _toward(d6, a0)
        vx, vy = r if r else (0, 0)
        m.wl(a2, (m.rl(a2) + (vx << 2)) & 0xffffffff)
        m.wl(a2 + 4, (m.rl(a2 + 4) + (vy << 2)) & 0xffffffff)
        return False
    if b:
        if not (-12 <= d6 < 28 and -8 <= a0 < 16 and z < 0x3fc4):
            return False
        _STALE[0] = s16(ox - m.rw(0x400684)); _STALE[1] = s16(oy - m.rw(0x400688))
    else:
        if not (-8 <= d6 < 16 and -12 <= a0 < 28 and z < 0x3fc4):
            return False
    if -12 <= _STALE[0] < 28 and -8 <= _STALE[1] < 16 and zprev < 0x3fc4:
        m.wb(a2 + 0x57, 0x65)                              # came down on top of it: broken
        state_entry_25bae(m, a2, 4)
    else:
        m.wb(FX, 1); m.wb(FY, 1)
    return False


def vacuum_pieces_13d38(m, a0):
    """FUN_13D38: the marble sucked into a vacuum - its 8 pieces (tiles 0x10B..0x112)
    leave one after another, two updates apart, first to the mouth and then up into
    the pipe, four updates for each leg.  True when it is over (+0x57 counts 0x20)."""
    t = 32 - s8(m.rb(a0 + 0x57))
    m.wb(a0 + 0x57, m.rb(a0 + 0x57) - 1)
    o = LEVEL_SLOTS[s8(m.rb(a0 + 0x58))]
    px, py = s16(m.rw(o + 0x4e)), s16(m.rw(o + 0x50))
    if m.rb(o + 0x1f) == 0x0d:
        px, py = s16(px - 5), s16(py + 5)
        mx, my = s16(px - 0x1c), s16(py - 0x0e)
    else:
        px, py = s16(px + 3), s16(py + 5)
        mx, my = s16(px + 0x1c), s16(py - 0x0e)
    sx, sy = s16(m.rw(a0 + 0x1e)), s16(m.rw(a0 + 0x20))
    leg1 = (s16(mx - sx), s16(my - sy))
    leg2 = (s16(px - mx), s16(py - my))
    for k in range(4):
        m.ww(a0 + 0x38 + 6 * k, 0); m.ww(a0 + 0xa4 + 6 * k, 0)
    n = 0
    for k in range(8):
        d1 = s16(t - 2 * k)
        if d1 >= 8:
            continue
        if d1 < 0:
            x, y = sx, sy
        elif d1 < 4:
            x, y = s16(((d1 * leg1[0]) >> 2) + sx), s16(((d1 * leg1[1]) >> 2) + sy)
        else:
            e = d1 - 4
            x, y = s16(((e * leg2[0]) >> 2) + mx), s16(((e * leg2[1]) >> 2) + my)
        slot = a0 + 0xa4 + 6 * n if n < 4 else a0 + 0x38 + 6 * (n - 4)
        n += 1
        m.ww(slot, 0x10b + k)
        m.ww(slot + 2, (s8(m.romb(0x1ef32 + 2 * k)) + x) & 0xffff)
        m.ww(slot + 4, (s8(m.romb(0x1ef33 + 2 * k)) + y) & 0xffff)
    m.wb(a0 + 0x1c, 1)
    return m.rb(a0 + 0x57) == 0


def zone_change_13966(m, a2):
    """FUN_13966 (a marble's zone changed, or it came back): music cues, and on Aerial
    the pop-up blocks (FUN_186AC) and restarting the vacuums of the new zone."""
    race = m.rw(0x400394)
    zone = m.rb(a2 + 0x1b)
    if race == 4:
        if (zone == 5 and not m.rb(0x40076a) & 1) or (zone == 6 and not m.rb(0x40076a) & 2):
            m.wb(0x40076a, m.rb(0x40076a) | (1 if zone == 5 else 2))
            m.sound(0x15 if zone == 5 else 0x16)      # Silly: fade its music / start 0x16
    if race != 3:
        return
    if zone == 0x14 and not m.rb(0x40076a) & 1:
        m.wb(0x40076a, m.rb(0x40076a) | 1)
        m.sound(0x0f)                                  # Aerial: fade its music
    elif zone in (6, 7) and not m.rb(0x40076a) & 2:
        m.wb(0x40076a, m.rb(0x40076a) | 2)
        m.ww(0x400768, 0x3c)
        m.sound(0x12)                                  # (a flag on the board, no sound)
    popups_zone_186ac(m)
    for o in LEVEL_SLOTS:
        if m.rb(o + 0x18) == 1 and m.rb(o + 0x1a) == 4 and m.rb(o + 0x1b) == zone:
            if m.rb(o + 0x1f) == 0x0b:
                m.wl(o + 0x36, 0x1d766)
            if m.rb(o + 0x1f) == 0x0d:
                m.wl(o + 0x36, 0x1d7ac)
            m.sound(m.read32(0x1ef42 + 4 * (s8(m.rb(o + 0x1b)) - 30)))   # vacuum hum on
            level_object_program_12896(m, o)


POPUP_SLOTS = [0x401650 + 16 * i for i in range(36)]


def popup_screen_18972(m, a):
    x, y = s8(m.rb(a + 4)) << 3, s8(m.rb(a + 5)) << 3
    m.ww(0x400690, x & 0xffff); m.ww(0x400692, y & 0xffff)
    sx = s16(y - x + 0x88)
    sy = s16(s16(m.rw(0x40097e)) + s16(m.rw(a + 6)) + 0x54 - ((x + y) >> 1))
    m.wl(a + 0x0c, ((sx & 0xffff) << 16) | (sy & 0xffff))


def popups_zone_186ac(m):
    """FUN_186AC: a marble in zone 4 or 5 sets the 36 pop-up blocks going (one of four
    timing sets for the last 12, picked at random); none there clears them."""
    if m.rw(0x400394) != 3:
        return
    there = any(m.rb(0x400018 + 0xe2 * i + 0x18) == 1 and m.rb(0x400018 + 0xe2 * i + 0x1b) in (4, 5)
                for i in range(s16(m.rw(0x400396))))
    if not m.rb(0x400760) and there:
        r = random_13a98(m, 4)
        m.wl(0x400764, m.read32(0x243e6 + 4 * r))
        for i, a in enumerate(POPUP_SLOTS):
            m.wb(a, i)
            m.ww(a + 2, m.romw(0x242c6 + 2 * i) if i < 24 else m.read16(m.rl(0x400764) + 2 * (i - 24)))
            m.wb(a + 4, m.romb(0x24406 + i)); m.wb(a + 5, m.romb(0x2442a + i))
            m.ww(0x400690, (s8(m.rb(a + 4)) << 3) & 0xffff)      # FUN_1BB28
            m.ww(0x400692, (s8(m.rb(a + 5)) << 3) & 0xffff)
            derive_sprite_fields(m)
            m.ww(a + 6, m.romw(0x2444e + 2 * i))
        m.wl(0x400764, m.read32(0x243f6 + 4 * r))
        m.wb(0x400760, 1)
    elif not there and m.rb(0x400760):
        for a in POPUP_SLOTS:
            m.ww(a + 6, 0); m.wb(a + 5, 0); m.wb(a + 4, 0); m.ww(a + 2, 0)
        m.wb(0x400760, 0)


def popups_update_1844a(m):
    """FUN_1844A (each tick on Aerial while they are on): each block counts down, then
    plays its rise-and-fall animation (0x21342) and waits again; a sound for the first
    of each group of 12 that starts."""
    if m.rw(0x400394) != 3 or not m.rb(0x400760):
        return
    first = [None, None, None]
    for i, a in enumerate(POPUP_SLOTS):
        tm = s16(m.rw(a + 2))
        if tm >= 0:
            m.ww(a + 2, tm - 1)
            if s16(m.rw(a + 2)) <= 0:
                m.wl(a + 8, 0x21342); m.ww(a + 2, 0xffff)
                g = 0 if i < 12 else (1 if i < 24 else 2)
                if first[g] is None:
                    first[g] = a
                popup_screen_18972(m, a)
        elif tm == -1:
            m.wl(a + 8, m.rl(a + 8) + 4)
            if m.read32(m.rl(a + 8)) == 0xffffffff:
                m.ww(a + 2, m.romw(0x242f6 + 2 * i) if i < 24 else m.read16(m.rl(0x400764) + 2 * (i - 24)))
        if s16(m.rw(a + 2)) == -1:
            popup_screen_18972(m, a)
    for g, a in enumerate(first):
        if a is not None:
            k = random_13a98(m, 2) + 2 * g                 # which of two sounds
            y = s16(m.rw(a + 0x0e))                        # off the screen: the quieter set
            m.sound(m.read32((0x1efd6 if y < -23 or y > 0xe0 else 0x1efb6) + 4 * k))


def popups_contact_1881c(m, a2):
    """FUN_1881C: a pop-up block that is up stops the marble at its cell's edge; one
    it is sitting on when it rises throws it into the air."""
    if m.rw(0x400394) != 3 or not m.rb(0x400760):
        return False
    hit = False
    cx, cy = m.rb(0x400697), m.rb(0x400699)
    px, py = (s32(m.rl(0x400684)) >> 19) & 0xff, (s32(m.rl(0x400688)) >> 19) & 0xff
    for a in POPUP_SLOTS:
        if s16(m.rw(a + 2)) != -1 or m.rb(a + 4) != cx or m.rb(a + 5) != cy:
            continue
        m.wl(a2 + 0x0c, m.rl(0x400684)); m.wl(a2 + 0x10, m.rl(0x400688))
        if m.rb(a + 4) == px and m.rb(a + 5) == py and m.rw(a + 6) == m.rw(a2 + 0x14):
            m.wl(a2 + 8, 0x70000)
            m.wl(a2 + 0x14, (m.rl(a2 + 0x14) + 0xc0000) & 0xffffffff)
            # The ROM tests the whole of d0 (tst.l) after FUN_13A98, which only sets its low
            # word: the high word is left over from before.  For x that is 0xFFFF (the loop
            # set d0 = -1), so x always gets +0x6000; for y it is the high word of the new
            # x velocity, so y gets -0x6000 only when that is 0 and the draw is 0.
            random_13a98(m, 2)
            vx = ((s32(m.rl(a2)) >> 1) + 0x6000) & 0xffffffff
            m.wl(a2, vx)
            r = random_13a98(m, 2) | (vx & 0xffff0000)
            d1 = 0x6000 if r else -0x6000
            m.wl(a2 + 4, ((s32(m.rl(a2 + 4)) >> 1) + d1) & 0xffffffff)
            m.wb(a2 + 0x36, 2)
            m.sound(0x45)                                  # thrown up by a pop-up
        elif s16(m.rw(a2 + 0x14)) - s16(m.rw(a + 6)) < 12:
            m.wl(a2, (-s32(m.rl(a2))) & 0xffffffff)
            m.wl(a2 + 4, (-s32(m.rl(a2 + 4))) & 0xffffffff)
        hit = True
    return hit


# Ultimate's marker areas: type -> (cells across, cells down, only at the object's height)
_MARK_AREAS = {0x32: (4, 2, False), 0x33: (2, 4, False), 0x34: (8, 11, False), 0x35: (3, 8, False),
               0x36: (6, 3, False), 0x37: (2, 3, False),
               0x2d: (20, 14, True), 0x2e: (2, 3, True), 0x2f: (10, 20, True), 0x30: (19, 9, True),
               0x31: (2, 6, True), 0x38: (20, 14, True), 0x39: (12, 14, True), 0x3a: (4, 2, True),
               0x3b: (2, 3, True)}


def _ultimate_hole(m, a2, o):
    """Handler 0x25 (0x2A87A): the marble drops through the hole (a 2x2-cell area, below
    height 0x3FE0) and rolls out of one of the two openings below - (0xF0, 0x100) or
    (0xF0, 0x140), picked at random; the other if a marble is already there."""
    d1 = s16((s16(m.rw(o + 0x0c)) >> 3) - s16(m.rw(0x400696)))
    d2 = s16((s16(m.rw(o + 0x10)) >> 3) - s16(m.rw(0x400698)))
    if not (0 <= d1 <= 1 and 0 <= d2 <= 1 and s16(m.rw(0x400694)) < 0x3fe0):
        return
    m.wb(a2 + 0x36, 0)
    m.sound(0x46)
    m.wb(a2 + 0x59, 0x12)
    m.wl(a2, 0x40000); m.wl(a2 + 4, 0)
    if random_13a98(m, 2):
        y = 0x140 if occupied_17cb8(m, a2, 0xf0, 0x100, 0x70) else 0x100
    else:
        y = 0x100 if occupied_17cb8(m, a2, 0xf0, 0x140, 0x70) else 0x140
    m.wl(a2 + 0x0c, 0xf00000); m.wl(a2 + 0x10, y << 16)
    sprite_pos_update_1bab2(m, a2)
    m.wl(a2 + 0x14, sprite_project_1cc62(m, 0))
    m.wb(a2 + 0x58, m.rb(o + 0x1f)); m.wb(a2 + 0x1a, 3)
    stop_roll_15884(m); m.sound(0x35)


POINTS = (250, 500, 750, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 0, 0, 0)


def points_285b0(m, a2, n):
    """FUN_285B0: award points (table 0x23CD4) and start the floating score sprite over
    the marble (frame from table 0x23CF6 at +0xD4, moved by +0x68/+0x69 until +0x70
    runs out - the +0xD8 effect in FUN_253EC)."""
    m.wl(a2 + 0xbc, (m.rl(a2 + 0xbc) + s16(m.romw(0x23cd4 + 2 * n))) & 0xffffffff)
    m.wb(0x40039c, m.rb(0x40039c) | (1 << m.rb(a2 + 0x19)))       # (score display to redraw)
    m.wl(a2 + 0xd4, m.read32(0x23cf6 + 4 * n))
    m.wb(a2 + 0x70, 0); m.wb(a2 + 0x68, 0); m.wb(a2 + 0x69, 0xff); m.wb(a2 + 0xd8, 1)


def practice_jump_bonus(m, a2):
    """Practice (0x1250A): landing after the jump in zone 1, 2 or 3 - once per player per
    zone (0x4003BA) - scores by how far it went: 250 per 4 pixels past the mark, from
    nothing up to 6000.  (The arcade also puts a little marker where it landed: the
    0x40098C slots, not drawn here.)"""
    bit = m.rb(a2 + 0x19) + 1
    zone = m.rb(a2 + 0x1b)
    d5 = -1
    if not m.rb(0x4003ba) & bit:
        m.wb(0x4003ba, m.rb(0x4003ba) | bit)
        if zone == 1:
            d5 = s16(m.rw(0x400692) - 0x1e8)
        elif zone == 2:
            d5 = s16(m.rw(0x400692) - 0x238)
        else:
            d5 = s16(m.rw(0x400690) - 0x210)
    if d5 >= 0:
        d5 >>= 2
    d5 = min(13, d5 + 7)
    if d5 > 6:
        points_285b0(m, a2, d5)


# ---------------------------------------------------------------------------
# Players: the loop over both marbles (FUN_251DE), the catch-up for a marble left
# behind in a two-player race (FUN_17934), and the race clocks (FUN_28A96).
# ---------------------------------------------------------------------------
PLAYERS = (0x400018, 0x4000fa)


def catchup_point_17934(m, a2):
    """FUN_17934: where a marble that fell behind comes back - the respawn point of the
    other player's zone (or one of the zones allowed with it) that is free and nearest
    the top of the window around the other marble's screen height.  Sets 0x400462/466
    (pixels) and 0x400472 (zone)."""
    other = PLAYERS[1 - s8(m.rb(a2 + 0x19))]
    race = m.rw(0x400394)
    top = s16(m.rw(other + 0x20))
    top = min(top, s16(m.romw(0x1f1d2))); top = max(top, s16(m.romw(0x1f1d0)))
    zone = m.rb(other + 0x1b)
    if race != 4:
        low = s16(top - 0x20)
        far = (race == 1 and zone in (0x0c, 0xff)) or (race == 2 and (s8(zone) > 8 or zone == 0xff))
        top = s16(top + (0xa0 if far else 0x40))
    else:
        low = s16(top - 0x40); top = s16(top + 0x20)
    zl = m.read32((0x24292 if m.rb(a2 + 0x19) else 0x24262) + 4 * race)
    zones = []
    while m.romb(zl) != 0xff:
        zones.append(m.romb(zl)); zl += 1
    ok_zone = zone in zones
    m.wl(0x400462, 0); m.wl(0x400466, 0); m.wb(0x400472, 0xff)

    def screen_y(tx, ty):
        x, y = tx * 8 + 4, ty * 8 + 4
        m.ww(0x400690, x); m.ww(0x400692, y)
        derive_sprite_fields(m)
        z = s32(sprite_project_1cc62(m, 1)) >> 16
        return x, y, s16(s16(m.rw(0x40097e)) + z + 0x54 - ((x + y) >> 1))

    table = m.read32(0x1ef1a + 4 * race)
    if ok_zone:
        a3 = table
        while m.romb(a3) != 0xff:
            if m.romb(a3 + 2) == zone:
                x, y, sy = screen_y(m.romb(a3), m.romb(a3 + 1))
                if not occupied_17cb8(m, a2, x, y, 0x180) and low < sy < top:
                    top = sy
                    m.wl(0x400462, x); m.wl(0x400466, y); m.wb(0x400472, m.romb(a3 + 2))
            a3 += 4
    if m.rb(0x400472) != 0xff:
        return
    fx = fy = 0; fz = 0
    a3 = table
    while m.romb(a3) != 0xff:
        allowed = m.romb(a3 + 2) in zones
        x, y, sy = screen_y(m.romb(a3), m.romb(a3 + 1))
        if fx == 0 and 8 < sy < 0xe8:
            fx, fy, fz = x, y, m.romb(a3 + 2)
        if not occupied_17cb8(m, a2, x, y, 0x180) and low < sy < top and allowed:
            top = sy
            m.wl(0x400462, x); m.wl(0x400466, y); m.wb(0x400472, m.romb(a3 + 2))
        a3 += 4
    if m.rl(0x400462) == 0:
        m.wl(0x400462, fx); m.wl(0x400466, fy); m.wb(0x400472, fz)


def hurry_event_1bbaa(m):
    """FUN_1BBAA: once a race (0x40076C, armed at the start), if the race's random
    number (0x400444) is below its threshold, a marble at rest in one of the listed
    zones with 3 to 6 seconds left sets off the +0xCB effect on every marble."""
    race = m.rw(0x400394)
    lst = m.read32(0x24aae + 4 * race)
    if not m.rb(0x40076c) or not m.romb(0x24a94 + race) > m.rb(0x400444) or m.romb(lst) == 0xff:
        return
    zones = []
    p = lst
    while m.romb(p) != 0xff:
        zones.append(m.romb(p)); p += 1
    n = s16(m.rw(0x400396))
    for a in PLAYERS[:n]:
        if (m.rb(a + 0x18) == 1 and m.rb(a + 0x1a) == 0 and 2 < s16(m.rw(a + 0x6a)) < 7
                and m.rb(a + 0x1b) in zones):
            m.wb(0x40076c, 0)
            for b in PLAYERS[:n]:
                if m.rb(b + 0x18) == 1:
                    m.wb(b + 0xcb, 1)


def players_251de(m):
    """FUN_251DE: each marble's update; in a two-player race a marble that has dropped
    off the bottom of the screen (the top on Silly) while on the ground is put back
    near the other one - 5 seconds off its clock, a death, the reappear animation.
    When every marble has finished or is out, the race is over (0x400390 = 3)."""
    hurry_event_1bbaa(m)
    n = s16(m.rw(0x400396))
    out = finished = 0
    for a2 in PLAYERS[:n]:
        if not m.rb(a2 + 0x18):
            out += 1
            continue
        if s16(m.rw(a2 + 0x6a)) > 0x190:
            m.unported.add('FUN_2822E (clock over 400)')
        marble_state_253ec(m, a2)
        if m.rb(a2 + 0x18) == 2:
            out += 1; continue
        if m.rb(a2 + 0x18) == 3:
            finished += 1; continue
        if n != 2:
            continue
        sy = s16(m.rw(a2 + 0x20))
        silly = m.rw(0x400394) == 4
        behind = (sy > 0xec and not silly) or (sy < -8 and silly)
        if not behind or m.rb(a2 + 0x36) == 2 or s8(m.rb(a2 + 0x1a)) not in (0, 1, 5):
            continue
        catchup_point_17934(m, a2)
        m.wl(a2 + 0x0c, (m.rl(0x400462) << 16) & 0xffffffff)
        m.wl(a2 + 0x10, (m.rl(0x400466) << 16) & 0xffffffff)
        m.ww(0x400698, 0xffff); m.ww(0x400696, 0xffff)
        sprite_pos_update_1bab2(m, a2)
        m.wl(a2 + 0x14, sprite_project_1cc62(m, 0))
        m.wb(a2 + 0x1b, m.rb(0x400472))
        for o in (8, 4, 0):
            m.wl(a2 + o, 0)
        m.wb(a2 + 0x57, 0); m.wb(a2 + 0x56, 0); m.wb(a2 + 0x36, 0); m.wb(a2 + 0x58, 0)
        m.wl(a2 + 0x26, 0); m.wl(a2 + 0x22, 0)
        screen_pos_1b9cc(m, a2)
        m.ww(a2 + 0x6a, (s16(m.rw(a2 + 0x6a)) - 5) & 0xffff)
        if s16(m.rw(a2 + 0x6a)) < 0:
            m.ww(a2 + 0x6a, 1); m.wb(a2 + 0x6c, 0); m.wb(a2 + 0x6e, 0)
        m.sound(0x3c)                                  # pulled forward
        m.wl(a2 + 0x5a, 0); m.wb(a2 + 0x1a, 4)
        m.ww(a2 + 0xd2, m.rw(a2 + 0xd2) + 1)
        m.wb(a2 + 0x57, 0x65)
        points_285b0(m, a2, 0x0f)
    if finished == n or (finished and out == n - 1):
        if m.rw(0x400390) != 1:
            m.ww(0x400390, 3)


def clock_tick_28c38(m, a):
    """FUN_28C38 on a clock (+0 seconds, +2 tenths, +4 frames; +4 = 0xFF: stopped):
    6 frames a tenth, 10 tenths a second.  Bit 0: ran out; bit 1: a second went."""
    if m.rb(a + 4) == 0xff:
        return 0
    m.wb(a + 4, m.rb(a + 4) - 1)
    if s8(m.rb(a + 4)) >= 0:
        return 0
    m.wb(a + 4, 5)
    m.wb(a + 2, m.rb(a + 2) - 1)
    if s8(m.rb(a + 2)) >= 0:
        return 0
    m.wb(a + 2, 9)
    m.ww(a, m.rw(a) - 1)
    return 3 if s16(m.rw(a)) == -1 else 2


def clocks_28a96(m):
    """FUN_28A96 (every frame): each player's clock (+0x6A) counts down; one that runs
    out puts that marble out (+0x18 = 2) and the race into its 'time up' state
    (0x400390 = 4), and anyone else still racing with under 5 seconds gets 5."""
    n = s16(m.rw(0x400396))
    expired = False
    for a2 in PLAYERS[:n]:
        a3 = a2 + 0x6a
        if m.rb(a2 + 0x1a) == 8:
            continue
        r = clock_tick_28c38(m, a3)
        if r & 1:
            m.wb(a2 + 0x18, 2); expired = True
            m.wb(a3 + 2, 0); m.ww(a3, 0); m.wb(a3 + 4, 0xff); m.wb(a2 + 0x71, 0xff)
        if r & 2:
            if s16(m.rw(a3)) == 4:
                m.wb(a2 + 0x71, 0)
            elif s16(m.rw(a3)) > 5:
                m.wb(a2 + 0x71, 0xff)
    if expired:
        m.ww(0x400390, 4)
        for a2 in PLAYERS[:n]:
            if m.rb(a2 + 0x18) == 1 and m.rb(a2 + 0x1a) != 6:
                if m.rw(a2 + 0x6a) == 0:
                    m.wb(a2 + 0x18, 2); m.wb(a2 + 0x6c, 0); m.ww(a2 + 0x6a, 0); m.wb(a2 + 0x6e, 0xff)
                elif s16(m.rw(a2 + 0x6a)) < 5:
                    m.ww(a2 + 0x6a, 5)


# ---------------------------------------------------------------------------
# The Silly Race's own objects (all of them return at once on any other race).
#   0x401890 (9 x 40 bytes): the little creatures in the pit - FUN_18FFA starts all
#     nine when the scroll enters rows 0x1D..0x38, FUN_190EE ends them on leaving.
#     Three each of kinds 7, 8, 9 (+0x25, also their display-list kind).  They wander
#     (or, while a marble is down in the pit, head for the nearest one); a marble
#     rolling over one squashes it: +3 seconds, points.
#   0x4019F8 (10 x 56 bytes, display kind 0xF): dropped at five fixed spots every 8
#     ticks while the scroll is in rows 3..0x1B (0x400762); they run down the course
#     (-y) until the floor rises in front of them, then splat.  One that touches a
#     marble takes it (marble state 0x0B, then it reappears).
# Fields: +0 vx, +4 vy, +0x0C x, +0x10 y, +0x14 z (16.16), +0x18 in use, +0x19 slot,
# +0x1A state, +0x1B counter, +0x1C animation pointer, +0x20 screen X | Y,
# +0x24 tick count, +0x25 kind / period, +0x26 direction (pit) or wall masks (0xF).
# ---------------------------------------------------------------------------
PIT_SLOTS = [0x401890 + 40 * i for i in range(9)]
DROP_SLOTS = [0x4019f8 + 56 * i for i in range(10)]


def _silly(m):
    return m.rw(0x400394) == 4


def pit_screen_199d6(m, a):
    """FUN_199D6 (and the first half of FUN_19E42): +0x20 = X << 16 | Y."""
    x, y = s16(m.rw(a + 0x0c)), s16(m.rw(a + 0x10))
    m.ww(0x400690, x); m.ww(0x400692, y)
    sx = s16(y - x + 0x88)
    sy = s16(m.rw(0x40097e) + s16(m.rw(a + 0x14)) + 0x54 - ((x + y) >> 1))
    m.ww(a + 0x20, sx); m.ww(a + 0x22, sy)


def _pit_outside_19460(x, y, m):
    """FUN_19460: off the pit floor (the 16 x 16 cell bitmap at 0x24496)."""
    cx = s8(((x >> 3) - 0x59) & 0xff)
    cy = s8(((y >> 3) - 0x5a) & 0xff)
    if not (0 <= cx <= 15 and 0 <= cy <= 15):
        return True
    return bool(m.romw(0x24496 + 2 * cy) & (1 << cx))


def _pit_crowded_193d8(m, a, x, y):
    """FUN_193D8: another creature (not squashed) within 12 units."""
    for o in PIT_SLOTS:
        if o == a or not m.rb(o + 0x18) or m.rb(o + 0x1a) == 2:
            continue
        if abs(s16(m.rw(o + 0x0c)) - x) < 12 and abs(s16(m.rw(o + 0x10)) - y) < 12:
            return True
    return False


def pit_blocked_1937c(m, a):
    x, y = s16(m.rw(a + 0x0c)), s16(m.rw(a + 0x10))
    return _pit_crowded_193d8(m, a, x, y) or _pit_outside_19460(x, y, m)


def pit_velocity_19976(m, a):
    """FUN_19976: the velocity for direction +0x26 (tables 0x244B6 / 0x244D6), and
    one step that way (kind 7 keeps a quarter of it as its velocity)."""
    d = s8(m.rb(a + 0x26))
    vx = s16(m.romw(0x244b6 + 2 * d)) << 8
    vy = s16(m.romw(0x244d6 + 2 * d)) << 8
    m.wl(a + 0x0c, (m.rl(a + 0x0c) + vx) & 0xffffffff)
    m.wl(a + 0x10, (m.rl(a + 0x10) + vy) & 0xffffffff)
    if m.rb(a + 0x25) == 7:
        vx >>= 2; vy >>= 2
    m.wl(a, vx & 0xffffffff); m.wl(a + 4, vy & 0xffffffff)


def _pit_stop(m, a):
    m.wb(a + 0x26, 0x10); m.wl(a + 4, 0); m.wl(a, 0)


def pit_try_19692(m, a):
    """FUN_19692: try the direction; when blocked, turn (kind 7 a step at a time, the
    others a quarter turn) up to 12 times, else stand still.  The position is only
    probed: it is put back either way."""
    if m.rb(a + 0x26) == 0x10:
        return
    x, y = m.rl(a + 0x0c), m.rl(a + 0x10)
    step = 1 if m.rb(a + 0x25) == 7 else 4
    pit_velocity_19976(m, a)
    if pit_blocked_1937c(m, a):
        for d2 in range(12):
            if m.rb(a + 0x25) != 7 and d2 & 3:
                continue
            m.wl(a + 0x0c, x); m.wl(a + 0x10, y)
            m.wb(a + 0x26, (m.rb(a + 0x26) + step) & 15)
            pit_velocity_19976(m, a)
            if not pit_blocked_1937c(m, a):
                break
        else:
            _pit_stop(m, a)
    m.wl(a + 0x0c, x); m.wl(a + 0x10, y)


def pit_toward_198bc(m, a):
    """FUN_198BC: as FUN_19692, but turning from a quarter turn back, skipping the
    direction it wanted, 9 tries."""
    if m.rb(a + 0x26) == 0x10:
        return
    x, y = m.rl(a + 0x0c), m.rl(a + 0x10)
    step = 1 if m.rb(a + 0x25) == 7 else 4
    pit_velocity_19976(m, a)
    if pit_blocked_1937c(m, a):
        want = m.rb(a + 0x26)
        m.wb(a + 0x26, (m.rb(a + 0x26) - 4) & 0xff)
        for d2 in range(9):
            if m.rb(a + 0x25) != 7 and d2 & 3:
                continue
            m.wl(a + 0x0c, x); m.wl(a + 0x10, y)
            m.wb(a + 0x26, (m.rb(a + 0x26) + step) & 15)
            if m.rb(a + 0x26) == want:
                continue
            pit_velocity_19976(m, a)
            if not pit_blocked_1937c(m, a):
                break
        else:
            _pit_stop(m, a)
    m.wl(a + 0x0c, x); m.wl(a + 0x10, y)


def pit_wander_1960e(m, a):
    """FUN_1960E: a new random direction (kind 9 sometimes stands still)."""
    if m.rb(a + 0x25) == 7:
        m.wb(a + 0x26, (random_13a98(m, 5) + m.rb(a + 0x26) - 2) & 15)
    else:
        moving = m.rl(a) != 0
        m.wb(a + 0x26, ((random_13a98(m, 2) << 3) + (4 if moving else 0)) & 0xff)
        if random_13a98(m, 4) == 0 and m.rb(a + 0x25) == 9:
            _pit_stop(m, a)
    pit_try_19692(m, a)


def pit_chase_1973c(m, a):
    """FUN_1973C: head for the nearest marble."""
    best, tgt = 0x140, PLAYERS[0]
    for p in PLAYERS[:s16(m.rw(0x400396))]:
        if not m.rb(p + 0x18):
            continue
        d2 = (abs(s16(m.rw(a + 0x0c)) - s16(m.rw(p + 0x0c))) << 4) & 0xffff
        d1 = (abs(s16(m.rw(a + 0x10)) - s16(m.rw(p + 0x10))) << 4) & 0xffff
        dist = (d2 + (d1 >> 3) * 3) if d2 >= d1 else (d1 + (d2 >> 3) * 3)
        dist &= 0xffff
        if dist < best:
            best, tgt = dist, p
    d4 = s16(m.rw(a + 0x0c) - m.rw(tgt + 0x0c))
    d3 = s16(m.rw(a + 0x10) - m.rw(tgt + 0x10))
    if m.rb(a + 0x25) == 7:
        ax, ay = abs(d4) >> 4, abs(d3) >> 4
        d = 0 if ay == 0 else (1 if ax > ay else (3 if ay > ax else 2))
        if d4 > 0 and d3 < 0:
            d += 12
        elif d4 < 0 and d3 < 0:
            d += 8
        elif d4 < 0 and d3 > 0:
            d += 4
    else:
        d4 >>= 4; d3 >>= 4
        d = 4 if d3 > 0 else (0x0c if d3 < 0 else (0 if d4 > 0 else 8))
    m.wb(a + 0x26, d)
    pit_toward_198bc(m, a)


def pit_animation_1953e(m, a):
    """FUN_1953E: the walk animation for the way it is going."""
    vx, vy = s16(m.rw(a)), s16(m.rw(a + 4))
    k = m.rb(a + 0x25)
    if k == 7:
        p = 0x21f72
    elif k == 8:
        p = 0x219b2 if vy > 0 else 0x21a32 if vy < 0 else 0x2197a if vx > 0 else 0x219ea if vx < 0 else 0x2194e
    else:
        p = 0x21e3e if vy > 0 else 0x21eb6 if vy < 0 else 0x21d4e if vx > 0 else 0x21dc6 if vx < 0 else 0x21f06
    m.wl(a + 0x1c, p)


def pit_next_194ba(m, a):
    """FUN_194BA: what next, by state (0 wander, 1 chase, 2 squashed)."""
    st = s8(m.rb(a + 0x1a))
    if st == 0:
        pit_wander_1960e(m, a); pit_animation_1953e(m, a)
    elif st == 1:
        pit_chase_1973c(m, a); pit_animation_1953e(m, a)
    elif st == 2:
        m.wl(a + 0x1c, {7: 0x21f8a, 8: 0x21a62}.get(m.rb(a + 0x25), 0x21efe))


def pit_start_18ffa(m):
    """FUN_18FFA: the nine creatures at random free spots in the pit."""
    if any(m.rb(a + 0x18) == 1 for a in PIT_SLOTS):
        return
    for i, a in enumerate(PIT_SLOTS):
        m.wb(a + 0x25, 7 if i < 3 else 8 if i < 6 else 9)
        m.wb(a + 0x18, 1); m.wb(a + 0x1a, 0)
        while True:
            m.wl(a + 0x0c, ((random_13a98(m, 0x20) * 4 + 0x2ca) << 16) & 0xffffffff)
            m.wl(a + 0x10, ((random_13a98(m, 0x20) * 4 + 0x2d2) << 16) & 0xffffffff)
            if not pit_blocked_1937c(m, a):
                break
        m.wl(a + 0x14, 0x3f6e0000)
        m.wl(a + 8, 0); m.wl(a + 4, 0); m.wl(a, 0); m.wb(a + 0x1b, 0)
        pit_next_194ba(m, a)
        pit_screen_199d6(m, a)


def pit_end_190ee(m):
    for a in PIT_SLOTS:
        m.wb(a + 0x18, 0)


def pit_update_1912c(m):
    """FUN_1912C, every tick: a marble on the pit floor (z 0x3F6E, layer 1) sets every
    creature chasing; they step every tick while chasing, every third otherwise."""
    if not _silly(m):
        return
    chase = 0
    for p in PLAYERS[:s16(m.rw(0x400396))]:
        if m.rb(p + 0x18) == 1 and m.rw(p + 0x14) == 0x3f6e and m.rb(p + 0x1b) == 1:
            chase = 1
    for a in PIT_SLOTS:
        if not m.rb(a + 0x18):
            continue
        m.wb(a + 0x24, m.rb(a + 0x24) + 1)
        if m.rb(a + 0x1a) != 2:
            m.wb(a + 0x1a, chase)
        limit = 1 if m.rb(a + 0x1a) == 1 else 3
        if s8(m.rb(a + 0x24)) >= limit:
            m.wb(a + 0x24, 0)
            if m.rb(a + 0x25) == 7 and m.rb(a + 0x1a) != 2:
                m.wl(a + 0x0c, (m.rl(a + 0x0c) + m.rl(a)) & 0xffffffff)
                m.wl(a + 0x10, (m.rl(a + 0x10) + m.rl(a + 4)) & 0xffffffff)
                m.wb(a + 0x1b, m.rb(a + 0x1b) + 1)
                if s8(m.rb(a + 0x1b)) >= 4:
                    pit_next_194ba(m, a)
                    m.wb(a + 0x1b, 0)
            else:
                m.wl(a + 0x1c, m.rl(a + 0x1c) + 4)
                if m.read32(m.rl(a + 0x1c)) == 0xffffffff:
                    if m.rb(a + 0x25) != 7:
                        m.wl(a + 0x0c, (m.rl(a + 0x0c) + m.rl(a)) & 0xffffffff)
                        m.wl(a + 0x10, (m.rl(a + 0x10) + m.rl(a + 4)) & 0xffffffff)
                    pit_next_194ba(m, a)
        pit_screen_199d6(m, a)


def pit_contact_1924e(m, a2):
    """FUN_1924E (a marble's update): a marble on layer 1 within 4 units (5 for kind
    7) of a creature squashes it: +3 seconds, points 0x0E and 500."""
    if not _silly(m) or m.rb(a2 + 0x1b) != 1:
        return
    px, py = s16(m.rw(0x400690)), s16(m.rw(0x400692))
    for a in PIT_SLOTS:
        if not m.rb(a + 0x18) or m.rb(a + 0x1a) == 2:
            continue
        r = 5 if m.rb(a + 0x25) == 7 else 4
        if r > (abs(px - s16(m.rw(a + 0x0c))) & 0xffff) and r > (abs(py - s16(m.rw(a + 0x10))) & 0xffff):
            m.wb(a + 0x1a, 2); m.wl(a + 4, 0); m.wl(a, 0)
            pit_next_194ba(m, a)
            if m.rb(a + 0x25) == 9:
                m.wl(a + 0x1c, 0x21efe)
                m.sound(0x5c)                              # squashed
            elif m.rb(a + 0x25) == 7:
                m.wl(a + 0x1c, 0x21f7a)
                m.sound(0x5b)
            else:
                m.sound(0x5a)
            m.ww(a2 + 0x6a, m.rw(a2 + 0x6a) + 3)
            points_285b0(m, a2, 0x0e)
            object_accum_flag_28608(m, a2, 0x1f4)


def drop_screen_19e42(m, a):
    """FUN_19E42: screen position, and wall masks in three columns of the course
    (cells x 0x29 / 0x31 / 0x39 from y 0x34 on), else none."""
    pit_screen_199d6(m, a)
    cx, cy = s8((s16(m.rw(a + 0x0c)) >> 3) & 0xff), s8((s16(m.rw(a + 0x10)) >> 3) & 0xff)
    if cx in (0x39, 0x31, 0x29) and cy >= 0x34:
        cover_264aa(m, a, 3)
    else:
        for k in range(3):
            m.ww(a + 0x26 + 6 * k, 0)


def drops_spawn_19a40(m):
    """FUN_19A40: up to two new ones, at the first of the five spots (0x244F6) with
    none in its column - or, second time round, with one at least 4 cells down."""
    for d4 in range(2):
        placed = False
        for d5 in range(5):
            cx, cy = s8(m.romb(0x244f6 + 2 * d5)), s8(m.romb(0x244f7 + 2 * d5))
            n, first = 0, None
            for i, a in enumerate(DROP_SLOTS):
                if m.rb(a + 0x18) == 1 and s16(m.rw(a + 0x0c)) >> 3 == cx:
                    n += 1
                    if n < 2:
                        first = i
            if n > d4:
                continue
            if n == 1 and cy - (s16(m.rw(DROP_SLOTS[first] + 0x10)) >> 3) < 4:
                continue
            a = next((a for a in DROP_SLOTS if m.rb(a + 0x18) != 1), None)
            if a is None:
                return
            m.wb(a + 0x18, 1); m.wb(a + 0x1a, 1)
            m.wl(a + 0x0c, ((cx << 19) + 0x40000) & 0xffffffff)
            m.wl(a + 0x10, ((cy << 19) + 0x40000) & 0xffffffff)
            m.wl(a + 0x14, 0x3fd80000)
            m.wb(a + 0x24, 0); m.wb(a + 0x1b, 0); m.wb(a + 0x25, 8)
            m.wl(a + 0x1c, 0x224ca); m.wl(a + 4, 0xfffc0000)
            drop_screen_19e42(m, a)
            m.sound(m.read32(0x24500 + 4 * d5))           # 0x54-0x58: dropped in
            placed = True
            break
        if not placed:
            continue


def drops_update_19baa(m):
    """FUN_19BAA, every tick."""
    if not _silly(m):
        return
    if m.rb(0x400762) and not (m.rl(0x400010) & 7):
        drops_spawn_19a40(m)
    for a in DROP_SLOTS:
        if not m.rb(a + 0x18):
            continue
        landed = False
        m.wb(a + 0x24, m.rb(a + 0x24) + 1)
        if s8(m.rb(a + 0x25)) <= s8(m.rb(a + 0x24)):
            m.wb(a + 0x24, 0)
            m.wl(a + 0x1c, m.rl(a + 0x1c) + 4)
            if m.read32(m.rl(a + 0x1c)) == 0xffffffff and m.rb(a + 0x1a) != 2:
                clear = 1                                  # no other one further down its column
                for b in DROP_SLOTS:
                    if b != a and m.rb(b + 0x18) == 1 and m.rw(b + 0x0c) == m.rw(a + 0x0c) \
                            and s16(m.rw(a + 0x10)) > s16(m.rw(b + 0x10)):
                        clear = 0
                        break
                m.wl(a + 0x1c, 0x224d6); m.wb(a + 0x1a, 0)
                if m.rb(a + 0x1b):
                    m.wb(a + 0x1b, m.rb(a + 0x1b) - 1)
                if not m.rb(a + 0x1b):
                    if m.rl(a + 4) == 0xfffe0000 and clear:
                        m.wb(a + 0x25, 1); m.wl(a + 4, 0xfffc0000)
                        m.wb(a + 0x1b, random_13a98(m, 2) + 4)
                    else:
                        m.wb(a + 0x25, 4); m.wl(a + 4, 0xfffe0000)
                        m.wb(a + 0x1b, random_13a98(m, 2) + 1)
            elif m.read32(m.rl(a + 0x1c)) == 0xffffffff and m.rb(a + 0x1a) == 2:
                m.wb(a + 0x18, 0)                          # splatted: gone
        if not m.rb(a + 0x1a):
            y0 = m.rl(a + 0x10)
            m.wl(a + 0x10, (y0 + m.rl(a + 4)) & 0xffffffff)
            object_pos_1bb08(m, a)
            if s32(sprite_project_1cc62(m, 1)) > s32(m.rl(a + 0x14)):   # the floor rises: splat
                m.wl(a + 0x10, y0 & 0xfffc0000)
                m.wl(a + 0x1c, 0x224e2); m.wb(a + 0x24, 0); m.wb(a + 0x25, 1); m.wb(a + 0x1a, 2)
                landed = True
        drop_screen_19e42(m, a)
        if landed and s16(m.rw(a + 0x0c)) >> 3 < 53 and 0 <= m.rw(a + 0x22) < 0xf0:
            m.sound(0x59)                                  # splat


def drops_contact_19d94(m, a2):
    """FUN_19D94 (a marble's update): touching a running one: it is taken (marble
    state 0x0B, +0x57 = 0x66), the dropped one bursts (0x22546)."""
    if not _silly(m):
        return
    px, py = s16(m.rw(0x400690)), s16(m.rw(0x400692))
    for a in DROP_SLOTS:
        if not m.rb(a + 0x18) or m.rb(a + 0x1a):
            continue
        y0 = s16(m.rw(a + 0x10) - 4); x0 = s16(m.rw(a + 0x0c) - 6)
        if x0 <= px < s16(x0 + 12) and y0 <= py < s16(y0 + 12):
            m.wb(a + 0x1a, 2); m.wl(a + 0x1c, 0x22546); m.wb(a + 0x25, 4); m.wb(a + 0x24, 0)
            m.wb(a2 + 0x1a, 0x0b); m.wb(a2 + 0x57, 0x66)
            m.sound(0x3e)                                  # taken


# ---------------------------------------------------------------------------
# The bouncing balls (end of the game, and the attract screens): up to 28 records
# of 10 bytes at 0x400A9C (the level objects' space, reused): +0 x, +2 y (pixels
# x 16), +4 / +6 velocity, +8 colour bits (group << 11).  0x4003E2 = how many.
# ---------------------------------------------------------------------------
BALLS = 0x400a9c


def balls_palette_26cfa(m):
    """FUN_26CFA: colours for the 8 ball groups: palette RAM 0xB00202 + 32 i, pens
    1-5 = 0xAFFF, 0xCFC0 and one of two triples from 0x20BB4 (a coin toss each).
    Returns {group: [5 words]} (the front end turns them into colours)."""
    out = {}
    for i in range(8):
        t = 0x20bb4 + 12 * i
        if random_13a98(m, 2):
            t += 6
        out[i] = [0xafff, 0xcfc0] + [m.romw(t + 2 * k) for k in range(3)]
    m.ball_palette = out
    return out


def balls_start_18cd2(m, n, colour):
    """FUN_18CD2: n balls at random places and speeds (never slower than 16 a frame
    on either axis); colour -1: random colours (and new ball colours), -2: one of
    the first two, else that colour."""
    if s8(colour) == -1:
        balls_palette_26cfa(m)
    for i in range(n):
        a = BALLS + 10 * i
        m.ww(a, ((random_13a98(m, 0x100) & 0xff) + 36) << 4)
        m.ww(a + 2, (random_13a98(m, 0x80) + 48) << 4)
        for o in (4, 6):
            v = s16(random_13a98(m, 0x60) - 0x30)
            m.ww(a + o, (v - 0x10 if v < 0 else v + 0x10) & 0xffff)
        if s8(colour) >= 0:
            c = s8(colour)
        elif s8(colour) == -1:
            c = random_13a98(m, 8)
        else:
            c = random_13a98(m, 2)
        m.ww(a + 8, (c << 11) & 0xffff)
    m.wb(0x4003e2, n)


def balls_step_18dca(m):
    """FUN_18DCA, every frame: each ball moves and bounces off the edges of the
    screen (x 8..0x148, y 0..0xE0 in pixels)."""
    for i in range(m.rb(0x4003e2)):
        a = BALLS + 10 * i
        nx = s16(m.rw(a + 4) + m.rw(a)); ny = s16(m.rw(a + 6) + m.rw(a + 2))
        px, py = nx >> 4, ny >> 4
        vx, vy = s16(m.rw(a + 4)), s16(m.rw(a + 6))
        if (px < 8 and vx < 0) or (px > 0x148 and vx > 0):
            m.ww(a + 4, -vx & 0xffff)
        if (py < 0 and vy < 0) or (py > 0xe0 and vy > 0):
            m.ww(a + 6, -vy & 0xffff)
        if 8 <= px <= 0x148 and 0 <= py <= 0xe0:
            m.ww(a, nx & 0xffff); m.ww(a + 2, ny & 0xffff)


# ---------------------------------------------------------------------------
# The attract-mode demo (race state 1): FUN_1815A steers the marble through a list
# of waypoints (0x1D364 per race -> 0x400446): cell x, cell y, speed, caption (-1
# none).  Within 32/16... of a waypoint it moves on to the next (showing the caption,
# BIOS 0x12A, for 90 frames); the list's end (0) ends the demo (0x40075A = 1).
# ---------------------------------------------------------------------------
def demo_steer_1815a(m, a2):
    while True:
        a3 = m.rl(0x400446)
        if not m.romb(a3):
            return
        d2 = s32((s8(m.romb(a3)) << 19) - s32(m.rl(a2 + 0x0c)) + 0x40000)
        d3 = s32((s8(m.romb(a3 + 1)) << 19) - s32(m.rl(a2 + 0x10)) + 0x40000)
        d5 = s8(m.romb(a3 + 2)) << 16
        d4 = (abs(d2) >> 12) & 0xffff
        d6 = (abs(d3) >> 12) & 0xffff
        if d4 < 32 and d6 < 32:                        # reached: the next waypoint
            cap = s8(m.romb(a3 + 3))
            if cap >= 0:
                m.demo_caption = (m.read32(0x242aa + 4 * cap), 0x3400, 0x5a)
            a3 += 4
            if not m.romb(a3):
                m.ww(0x40075a, 1)
                m.wb(a2 + 0x6e, 0xff)
            m.wl(0x400446, a3)
            continue
        if d4 > d6:
            d1 = ((d6 >> 3) * 3 + d4) & 0xffff
        else:
            d1 = ((d4 >> 3) * 3 + d6) & 0xffff
        if d1:
            d2 = s16(_divs(d2, s16(d1))); d3 = s16(_divs(d3, s16(d1)))
        else:
            d2 = d3 = 0
        if d1 < 64:                                     # close: slow down
            d5 = 0xc000
        k = s16((d5 >> 8) & 0xffff)
        d2 = s32(k * d2) >> 4
        d3 = s32(k * d3) >> 4
        vx, vy = s32(m.rl(a2)), s32(m.rl(a2 + 4))
        m.wl(a2, (vx + ((d2 - vx) >> 3)) & 0xffffffff)
        m.wl(a2 + 4, (vy + ((d3 - vy) >> 3)) & 0xffffffff)
        if m.rb(a2 + 0x36):
            vz = s32(m.rl(a2 + 8)) - 0x6000
            m.wl(a2 + 8, max(vz, -0x50000) & 0xffffffff)
        flag_scaled_magnitude(m, a2)
        return
