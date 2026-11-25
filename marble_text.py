"""Atari System 1 text layer (the "alphanumerics") for Marble Madness.

The hardware has a 64 x 32 map of 8 x 8 characters at 0xA03000 (one word each)
drawn over the playfield and sprites, never scrolled.  A word is
    bits 0-9   character (the 2-bit graphics in ROM 136032.104, 16 bytes each)
    bits 10-12 colour (4 pens each, palette entries 0-31)
    bit 13     opaque: pen 0 is drawn too (the solid boxes behind the text)
The BIOS prints with FUN_2572 (vector 0x142): a message is a chain of records
    +0 column, +1 row, +2 string pointer (ASCII, 0-terminated), +6 'more' flag,
    +8 next record,
each character written as (attribute | code); with attribute bits 14-15 set the
letters A-Z move by 0x40 (another set of letters in the ROM).
"""
import numpy as np


def decode_chars(rom104):
    """136032.104 -> array [n, 8, 8] of pens 0-3 (MAME's 'anlayout': planes at bit 0
    and bit 4 of each nibble pair, 16 bytes a character)."""
    n = len(rom104) // 16
    out = np.zeros((n, 8, 8), np.uint8)
    for c in range(n):
        for y in range(8):
            b0, b1 = rom104[16 * c + 2 * y], rom104[16 * c + 2 * y + 1]
            for x in range(8):
                byte = b0 if x < 4 else b1
                k = x & 3
                hi = (byte >> (7 - k)) & 1           # plane at bit offset 0
                lo = (byte >> (3 - k)) & 1           # plane at bit offset 4
                out[c, y, x] = (hi << 1) | lo
    return out


def print_message(alpha, rom, msg, attr, orient=0, yoff=0):
    """FUN_2572: write a message chain into alpha RAM (bytearray of 0x1000).
    `rom` is the CPU image (for the strings and the BIOS tables)."""
    w = lambda a: int.from_bytes(rom[a:a + 2], 'big')
    lim, step, shift, xor = (w(0x7294 + 2 * orient), w(0x72a0 + 2 * orient),
                             rom[0x72a4 + 2 * orient + 1], w(0x72a8 + 2 * orient))
    a = msg
    while True:
        col, row = rom[a], rom[a + 1]
        row = (row - 128 if row > 127 else row) - yoff
        if row > lim:
            break
        p = int.from_bytes(rom[a + 2:a + 6], 'big')
        base = (41 - row) if orient else (row << 6)
        pos = (col << shift) + base
        while rom[p]:
            ch = rom[p]; p += 1
            if ch == 0x20:
                word = attr
            else:
                if attr & 0xc000 and 0x41 <= ch <= 0x5a:
                    ch = ch - 0x40 if attr & 0x8000 else ch + 0x40
                word = ((attr | ch) ^ xor) & 0xffff
            if 0 <= pos < 0x800:
                alpha[2 * pos:2 * pos + 2] = word.to_bytes(2, 'big')
            pos += step
        if rom[a + 6] + 0 <= 1:                       # (+6 + 0x401F00 > 1: carry on)
            break
        a = int.from_bytes(rom[a + 8:a + 12], 'big')
    return True


def palette_from_ram(pal_ram):
    """Palette RAM words (IIII RRRR GGGG BBBB) -> RGB for the first 32 entries."""
    zt = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
    out = np.zeros((32, 3), np.uint8)
    for i in range(32):
        v = int.from_bytes(pal_ram[2 * i:2 * i + 2], 'big')
        s = zt[v >> 12]
        out[i] = (((v >> 8) & 15) * s, ((v >> 4) & 15) * s, (v & 15) * s)
    return out


def render(alpha, chars, pal, cols=range(3, 39), rows=range(30)):
    """The text layer as RGBA (transparent where nothing is drawn), for the screen
    columns `cols` (our 288-pixel picture is MAME's columns 3..38)."""
    w, h = 8 * len(cols), 8 * len(rows)
    img = np.zeros((h, w, 4), np.uint8)
    for j, r in enumerate(rows):
        for i, c in enumerate(cols):
            v = int.from_bytes(alpha[2 * (64 * r + c):2 * (64 * r + c) + 2], 'big')
            code, colour, opaque = v & 0x3ff, (v >> 10) & 7, v & 0x2000
            if code >= len(chars):
                code &= len(chars) - 1
            t = chars[code]
            if not opaque and not t.any():
                continue
            rgb = pal[colour * 4 + t]
            a = np.where((t > 0) | bool(opaque), 255, 0).astype(np.uint8)
            img[8 * j:8 * j + 8, 8 * i:8 * i + 8, :3] = rgb
            img[8 * j:8 * j + 8, 8 * i:8 * i + 8, 3] = a
    return img


# ---------------------------------------------------------------------------
# The BIOS text calls the game uses, and the game's own HUD routines.
# ---------------------------------------------------------------------------
class TextLayer:
    """Alpha RAM plus the routines that write it (orientation 0: Marble's screen)."""
    def __init__(self, rom):
        self.rom = rom
        self.alpha = bytearray(0x1000)

    def w(self, a): return int.from_bytes(self.rom[a:a + 2], 'big')
    def l(self, a): return int.from_bytes(self.rom[a:a + 4], 'big')

    def put(self, x, y, word):                      # one cell
        pos = 64 * y + x
        if 0 <= pos < 0x800:
            self.alpha[2 * pos:2 * pos + 2] = (word & 0xffff).to_bytes(2, 'big')

    def putchar_218(self, x, y, ch, attr):          # BIOS 0x3784
        self.put(x, y, attr | ch)

    def message(self, rec, attr):                   # BIOS 0x2572 (vector 0x142), a ROM record
        print_message(self.alpha, self.rom, rec, attr)

    def text(self, x, y, s, attr):                  # 0x142 on a RAM record (buffer + position)
        for i, ch in enumerate(s.encode('latin1')):
            if ch == 0x20:
                word = attr
            else:
                if attr & 0xc000 and 0x41 <= ch <= 0x5a:
                    ch = ch - 0x40 if attr & 0x8000 else ch + 0x40
                word = attr | ch
            self.put(x + i, y, word)

    def big(self, x, y, s, attr):                   # BIOS 0x3520 (vector 0x200)
        for ch in s.encode('latin1'):
            k = self.l(0x72ac + 4 * ch)
            if 39 <= k <= 46:                       # one column wide, two tall
                self.put(x, y, (self.w(0xe04 + 4 * (k - 39)) + 0x100) | attr)
                self.put(x, y + 1, (self.w(0xe04 + 4 * (k - 39) + 2) + 0x100) | attr)
                x += 1
            elif k == 50:
                for dx, dy, c in ((0, 0, 0x1c), (1, 0, 0xdb), (0, 1, 0x1e), (1, 1, 0xdd)):
                    self.put(x + dx, y + dy, attr | c)
                x += 2
            else:
                t = 0xccc + 8 * k
                for i, (dx, dy) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
                    self.put(x + dx, y + dy, (self.w(t + 2 * i) + 0x100) | attr)
                x += 2

    @staticmethod
    def fmt_112(value, kind='d', flags=0, width=7):
        """BIOS 0x3874, decimal: flags 1 = leading zeros, no commas; 2 = left-justified;
        0 = right-justified with spaces; commas between thousands unless flags 1."""
        value &= 0xffffffff
        d2 = width
        if flags != 1:
            d2 += (width - 1) // 3
        fill = 0x30 if flags == 1 else 0x20
        buf = [fill] * d2
        p = 0
        powers = [10 ** k for k in range(9, 0, -1)][10 - width:]
        started = bool(flags & 4)
        f = flags
        for pw in powers:
            d = 0
            while value >= pw:
                value -= pw; d += 1
            comma = pw in (10 ** 9, 10 ** 6, 10 ** 3)
            if d == 0 and not (f & 4):
                if f == 2:
                    continue
                if f == 0:
                    p += 1
                    if comma:
                        p += 1
                    continue
            f |= 4
            buf[p] = 0x30 + d; p += 1
            if not (f & 1) and comma:
                buf[p] = 0x2c; p += 1
        buf[p] = 0x30 + value; p += 1
        return bytes(buf).decode('latin1')

    # -- the game's routines -------------------------------------------------------
    def frame_283c2(self, players=1):
        """FUN_283C2: the black edges (columns 0-2 and 39-41) and the timer's box."""
        for y in range(30):
            for x in (0, 1, 2, 39, 40, 41):
                self.put(x, y, 0x3400)
        xs, n = (0x23c2c, 12) if players == 1 else (0x23ca4, 24)
        for i in range(n):
            self.putchar_218(self.w(xs + 2 * i), self.w(0x23c44 + 2 * i), self.w(0x23c74 + 2 * i), 0x1c00)

    def score_box_106(self, players=1):
        """Race start (0x1068C): the score boxes' top line and 'SCORE'."""
        self.message(0x22b16, 0x1c00); self.message(0x22b22, 0x2000)
        if players == 2:
            self.message(0x22b2e, 0x1c00); self.message(0x22b3a, 0x2400)

    def score_28624(self, player, score):
        """FUN_28624 -> 0x28E3C: the score, left-justified, 7 digits with commas."""
        s = self.fmt_112(score, 'd', 2, 7)
        s = s.split(' ')[0] if ' ' in s else s          # 0x28F28: cut at the first space
        x = self.rom[0x23d3a + player]
        self.text(x, 2, s, 0x2400 if player else 0x2000)

    def timer_286ee(self, which, seconds):
        """FUN_286EE: the timer in big digits (two, leading zero)."""
        s = self.fmt_112(min(seconds, 99), 'd', 1, 2)
        attr = 0x2c00 if which == 2 else (0x3400 if which == 3 else 0x2800)
        x = self.rom[0x23d3c + which]
        self.big(x, 0 if which == 3 else 1, s, attr)


# -- colours, messages that come and go, the race intro and the bonus --------------
def _tl_init_colours(self):
    self.pal = [self.w(0x1fbd0 + 2 * i) for i in range(32)]   # the text colours (ROM 0x1FBD0)


def _tl_bonus_colours(self):
    """0x118DA: the colours while the bonus is up."""
    for i in (0, 4, 29, 8, 12):
        self.pal[i] = 0
    self.pal[9] = self.pal[13] = 0xafff
    self.pal[11] = 0xf00f; self.pal[15] = 0xaf00


def _tl_normal_colours(self):
    """0x11A76: back to the racing colours."""
    for i in (0, 4, 29, 8, 12):
        self.pal[i] = 0xafff
    self.pal[3] = 0xf00f; self.pal[7] = 0xaf00
    self.pal[9] = self.pal[13] = 0


def _tl_erase(self, rec):
    """BIOS 0x2ABC (vector 0x118): blank the cells a message chain covers."""
    rom = self.rom
    a = rec
    while True:
        col, row = rom[a], rom[a + 1]
        p = int.from_bytes(rom[a + 2:a + 6], 'big')
        pos = 64 * row + col
        while rom[p]:
            if 0 <= pos < 0x800:
                self.alpha[2 * pos:2 * pos + 2] = b'\0\0'
            pos += 1; p += 1
        if rom[a + 6] <= 1:
            break
        a = int.from_bytes(rom[a + 8:a + 12], 'big')


def _tl_clear(self):
    self.alpha[:] = bytes(0x1000)


def _tl_big_number(self, value, x, y, width, attr, flags=0):
    """0x28EB2: a number in big digits (0x112 then 0x200)."""
    s = self.fmt_112(value, 'd', flags, width)
    if flags == 2 and ' ' in s:
        s = s.split(' ')[0]
    self.big(x, y, s, attr)


def _tl_number(self, value, x, y, width, attr, flags=0):
    """0x28E3C: a number in ordinary characters."""
    s = self.fmt_112(value, 'd', flags, width)
    if flags == 2 and ' ' in s:
        s = s.split(' ')[0]
    self.text(x, y, s, attr)


def _tl_race_intro(self, race, seconds):
    """0x10900: the banner - 'TIME TO FINISH' (Practice, Beginner) or 'EXTRA TIME FOR'
    (the rest), the race's name, and the seconds in big digits at (29, 9)."""
    self.intro_recs = (0x2291e if race < 2 else 0x22942, self.l(0x1f15e + 4 * race), self.l(0x1f176 + 4 * race))
    self.message(self.intro_recs[0], 0x3000)
    self.message(self.intro_recs[1], 0x3000)
    self.message(self.intro_recs[2], 0x3400)
    self.big_number(seconds, 0x1d, 9, 2, 0x3400)


def _tl_race_intro_end(self):
    for r in self.intro_recs:
        self.erase(r)


def _tl_bonus(self, player, amount):
    """0x11940: 'BONUS FOR / TIME LEFT:' and the amount, in the bonus colours."""
    self.bonus_colours()
    self.message(0x22b82 if player == 0 else 0x22b9a, 0x2000 if player == 0 else 0x2400)
    self.number(amount, self.rom[0x1d36e + player], 4, 5, 0x2400 if player else 0x2000)


def _tl_image(self, chars, cols=range(0, 42)):     # the whole 336-pixel screen
    zt = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
    pal = np.array([(((v >> 8) & 15) * zt[v >> 12], ((v >> 4) & 15) * zt[v >> 12], (v & 15) * zt[v >> 12])
                    for v in self.pal], np.uint8)
    return render(self.alpha, chars, pal, cols)


for _n, _f in (('init_colours', _tl_init_colours), ('bonus_colours', _tl_bonus_colours),
               ('normal_colours', _tl_normal_colours), ('erase', _tl_erase), ('clear', _tl_clear),
               ('big_number', _tl_big_number), ('number', _tl_number), ('race_intro', _tl_race_intro),
               ('race_intro_end', _tl_race_intro_end), ('bonus', _tl_bonus), ('image', _tl_image)):
    setattr(TextLayer, _n, _f)
_orig_init = TextLayer.__init__


def _tl_new_init(self, rom):
    _orig_init(self, rom)
    self.init_colours()
    self.intro_recs = ()


TextLayer.__init__ = _tl_new_init


class HighScores:
    """The high-score table (the arcade keeps it in EEPROM through BIOS 0x1AE / 0x1B4 /
    0x1C6; here a small JSON file next to the game).  The factory table is the ROM's
    (0x1EEA0: 10 x score long + 3 initials)."""
    def __init__(self, rom, path=None):
        self.rom = rom
        self.default = [(int.from_bytes(rom[0x1eea0 + 8 * i:0x1eea4 + 8 * i], 'big'),
                         rom[0x1eea4 + 8 * i:0x1eea7 + 8 * i].decode('latin1')) for i in range(10)]
        self.path = path
        self.entries = list(self.default)
        if path:
            try:
                import json
                data = json.load(open(path))
                self.entries = [(int(sc), str(nm)[:3].ljust(3)) for sc, nm in data][:10]
            except Exception:
                pass

    def save(self):
        if self.path:
            try:
                import json
                json.dump(self.entries, open(self.path, 'w'))
            except Exception:
                pass

    def rank(self, score):
        """BIOS 0x1C6: the place a score would take (0-9), 10 if none; a later equal
        score goes below an earlier one."""
        if score > 0xffffff:
            return -1
        for i, (sc, _) in enumerate(self.entries):
            if sc < score:
                return i
        return 10

    def insert(self, rank, score, initials):
        """BIOS 0x1B4."""
        if 0 <= rank < 10:
            self.entries.insert(rank, (score, initials))
            del self.entries[10:]
            self.save()

    def is_default(self):
        return self.entries == self.default


def _tl_hs_table(self, table, rank=-1, entry=False):
    """FUN_11FF8: the heading - TROUBLEMAKERS while the table is the factory one
    (not while entering), else HIGH ROLLERS at row 9 (3 while entering) - and the ten
    rows from row 11 (13 while entering); with a rank, the rows below it show the
    entry above (room made for the new one)."""
    if table.is_default() and not entry:
        self.message(0x228fa, 0x1400)
    else:
        self.text(15, 3 if entry else 9, self.rom_str(0x22ea2), 0x1400)
    y0 = 13 if entry else 11
    for i in range(10):
        sc, name = table.entries[i - 1] if (rank != -1 and i > rank) else table.entries[i]
        num = f' #{i + 1}' if i < 9 else f'#{i + 1}'
        self.text(13, y0 + i, f'{num} {name:3}   ' + self.fmt_112(sc, 'd', 0, 5), 0x1000)


TextLayer.hs_table = _tl_hs_table


def _tl_rom_str(self, ptr):
    """A message's text through a ROM pointer (the tables at 0x22E8E on)."""
    p = self.l(ptr)
    out = bytearray()
    while self.rom[p]:
        out.append(self.rom[p]); p += 1
    return out.decode('latin1')


def _tl_time_up(self, players, out):
    """FUN_16A20, first part: a box for each player whose time has run out, in the
    player's column (0x2399C) and colour - 'LEFT/RIGHT PLAYER' (two players only),
    'OUT OF TIME', 'GAME OVER', with blank rows between."""
    for i in out:
        col = self.rom[0x2399c + players + i - 1]
        attr = 0x2400 if i else 0x2000
        rows = [(0x18, 0x22e96), (0x19, 0x22e8e), (0x1a, 0x22e96), (0x1b, 0x22e92), (0x1c, 0x22e96)]
        if players == 2:
            rows = [(0x16, 0x22e96), (0x17, self.l(0x1eef0 + 4 * i))] + rows
        for row, ptr in rows:
            self.text(col, row, self.rom_str(ptr), attr)


def _tl_ready(self, players, i, word=None):
    """FUN_16A20, the restart of the player still racing: 'LEFT/RIGHT PLAYER' above
    READY, SET, GO! (row 0x1B)."""
    col = self.rom[0x2399c + players + i - 1]
    attr = 0x2400 if i else 0x2000
    if word is None:
        self.text(col, 0x18, self.rom_str(0x22e96), attr)
        self.text(col, 0x19, self.rom_str(self.l(0x1eef0 + 4 * i)), attr)
        self.text(col, 0x1a, self.rom_str(0x22e96), attr)
        self.text(col, 0x1b, self.rom_str(0x22ea6), attr)
        self.text(col, 0x1c, self.rom_str(0x22e96), attr)
    else:
        self.text(col, 0x1b, self.rom_str({'SET': 0x22eaa, 'GO': 0x22eae}[word]), attr)


def _tl_clear_rows(self, first=0x14):
    """FUN_16E8E: rows first..29 cleared between the side edges (columns 3-38)."""
    for y in range(first, 0x1e):
        for x in range(3, 3 + 0x24):
            self.put(x, y, 0)


def _tl_big_message(self, rec, attr):
    """BIOS 0x200 on a ROM record chain: big letters at the record's column and row."""
    while True:
        p = self.l(rec + 2)
        txt = bytearray()
        while self.rom[p]:
            txt.append(self.rom[p]); p += 1
        self.big(self.rom[rec], self.rom[rec + 1], txt.decode('latin1'), attr)
        if not self.rom[rec + 6]:
            break
        rec = self.l(rec + 8)


def _tl_end_screen(self, i, players, seconds, deaths):
    """FUN_18A88 for a player who finished the Ultimate Race: the screen cleared,
    CONGRATULATIONS! (big), YOU HAVE COMPLETED THE ULTIMATE RACE!, the bonus lines
    (20,000 for finishing, seconds left x 1000 and times died x -1000, both capped at
    99 / 20) and the TOTAL, then FINAL SCORE: (big).  Returns the total."""
    for pos in range(0x780):                       # FUN_28C7E(0): every cell
        self.put(pos % 64, pos // 64, 0)
    d5, d6 = (0x1400, 0x1000) if i else (0x1000, 0x1400)
    self.big_message(0x22b0a, d5)
    self.message(0x22aaa, d6)
    if players == 2:
        self.text(12, 5, self.rom_str(self.l(0x1eef0 + 4 * i)), 0x2400 if i else 0x2000)
    total = 20000
    secs = min(seconds, 99)
    self.number(secs, 10, 15, 2, d5, flags=1)
    self.number(secs * 1000, 0x21, 15, 5, d6)
    total += secs * 1000
    died = min(deaths, 20)
    self.number(died, 12, 0x11, 2, d5)
    self.number(died * 1000, 0x21, 0x11, 5, d6)
    total -= died * 1000
    self.number(total, 0x20, 0x13, 6, d6)
    self.big_message(0x22af2, d5)
    self.big_message(0x22afe, d5)
    self.end_attrs = (d5, d6)
    return total


def _tl_end_count(self, total, score):
    """One step of the count (every 2 frames): the total less 250, the score in big
    digits."""
    d5, d6 = self.end_attrs
    self.number(total, 0x20, 0x13, 6, d6)
    self.big_number(score, 0x17, 0x18, 7, d5)


TextLayer.big_message = _tl_big_message
TextLayer.end_screen = _tl_end_screen
TextLayer.end_count = _tl_end_count
TextLayer.rom_str = _tl_rom_str
TextLayer.time_up = _tl_time_up
TextLayer.ready = _tl_ready
TextLayer.clear_rows = _tl_clear_rows


class ArcadeHud:
    """The arcade's text screens during a game, driven frame by frame by the front end:
    the attract screen (0x11654: high scores, credits, the flashing PUSH START), the
    race intro (0x10900: banner, 60 frames, then 5 seconds moved to each timer every
    10 frames, then 60 - seconds frames), the scores and timers, and the bonus."""
    def __init__(self, rom, chars, players=1):
        self.t = TextLayer(rom)
        self.chars = chars
        self.players = players
        self.surface_key = None
        self.image = None
        self.phase = None
        self.timed = []                      # BIOS 0x100 messages: [record, frames left]
        self.attract_t = 0

    # -- the attract screen -------------------------------------------------------------
    def attract(self, credits, table):
        """The high-score page of the attract loop (0x400392 = 2: every colour is the
        racing one less 0x2000, and the ____ lines are not drawn)."""
        t = self.t
        t.clear(); t.normal_colours(); self.timed = []; self.phase = None; self.pending = []
        t.hs_table(table)
        t.message(0x22a32, 0x1000)                             # (c) line
        self.credits(credits)
        self.attract_t = 0

    def credits(self, credits, d2=0x2000):
        """FUN_28232: the coin line - on the high-score page (0x400392 = 2) every colour
        less 0x2000 and no ____ lines; in the demo with them."""
        t = self.t
        if not d2:
            t.message(t.l(0x23c04 + 4), 0x1800)
        t.message(t.l(0x23c18 + 4), 0x3400 - d2)               # 1 COIN PER PLAY
        if not d2:
            t.message(0x228ca, 0x1800)
        t.message(0x228d6, 0x3400 - d2)                        # CREDITS:
        t.text(32, 28, t.fmt_112(min(credits, 99), 'd', 0, 3) + '    ', 0x3400 - d2)
        self.n_credits = credits

    def demo_start(self, credits):
        """The attract demo's screen: the one-player score box and timer, the ____ and
        (c) lines (0x11654 with 0x400392 = 0) and the coin line."""
        t = self.t
        t.clear(); t.normal_colours(); self.timed = []; self.phase = None; self.pending = []
        self.players = 1
        t.frame_283c2(1); t.score_box_106(1)
        t.score_28624(0, 0); t.timer_286ee(0, 60)
        t.message(0x22a26, 0x1800); t.message(0x22a32, 0x3000)
        self.credits(credits, d2=0)
        self.shown = ((0,), (60,)); self.active = [True]

    def caption(self, rec, attr, frames):
        """BIOS 0x12A: a message that goes again after so many frames."""
        self.t.message(rec, attr)
        self.timed.append([rec, frames])

    def credits_page(self):
        """Every eighth turn of the attract loop (0x11604): the people who made it."""
        t = self.t
        t.clear(); t.normal_colours(); self.timed = []; self.phase = None; self.pending = []
        t.message(0x22d26, 0x3000)
        t.message(0x22d32, 0x3400)

    def attract_step(self):
        """The PUSH START lines: three rows put up 10 frames apart and held 30 frames,
        once a second, while there is a credit (0x116BC)."""
        self.tick_timed()
        self.attract_t += 1
        if self.attract_t % 60 == 1 and self.n_credits:
            recs = (0x22a7a, 0x22a86, 0x22a92) if self.n_credits >= 2 else (0x22a56, 0x22a62, 0x22a6e)
            self.pending = [(0, recs[0], 0x1800), (10, recs[1], 0x1000), (20, recs[2], 0x1c00)]
            self.pend_t = 0
        if self.pending:
            for item in list(self.pending):
                if item[0] == self.pend_t:
                    self.t.message(item[1], item[2]); self.timed.append([item[1], 30])
                    self.pending.remove(item)
            self.pend_t += 1

    # -- entering the high-score table (FUN_11B18) ---------------------------------
    def hs_entry(self, table, i, players, score, initials):
        """A generator, one step a frame; before each step the front end puts the
        frame's trackball movement and start buttons in self.entry_input =
        (x, y, start) - x, y signed counts as the arcade reads them (0x4006AA /
        0x4006A8), start True on the frame a start button went down.  Returns
        (rank, initials) through StopIteration.value, or None if the score does not
        make the table.  The 45-second clock shows from 10 seconds in big digits."""
        t = self.t
        rank = table.rank(score)
        if rank == 10:
            return None
        rank = max(rank, 0)
        t.hs_table(table, rank, entry=True)
        if players == 2:
            t.text(12, 4, t.rom_str(t.l(0x1eef0 + 4 * i)), 0x2400 if i else 0x2000)
        for x in range(16):                                    # its row in the table
            t.put(13 + x, 13 + rank, 0)
        name = list((initials + '   ')[:3].encode('latin1'))
        num = f' #{rank + 1}' if rank < 9 else f'#{rank + 1}'
        t.big(6, 6, num + ' ' + bytes(name).decode('latin1'), 0x1000)
        t.big_number(score, 0x13, 6, 7, 0x1000)
        t.message(0x22b76, 0x1400)                             # PRESS START 1 OR START 2 TO ADVANCE
        t.message(0x228ee, 0x1400)                             # ENTER THE RANKS OF THE
        clock = [0x2d, 9, 5]                                   # 45 s (0x40039E)
        pos, acc, n = 0, 0, 0
        t.big(13, 6, chr(name[0]), 0x1400)

        def tick_clock():
            clock[2] -= 1
            if clock[2] >= 0:
                return False
            clock[2] = 5; clock[1] -= 1
            if clock[1] >= 0:
                return False
            clock[1] = 9; clock[0] -= 1
            if clock[0] == -1:
                return True
            if clock[0] < 11:
                t.timer_286ee(3, clock[0])
            return False
        expired = False
        while pos < 3 and not expired:
            yield
            expired = tick_clock()
            if expired:
                break
            n += 1
            x, y, start = self.entry_input
            d1 = (-y - x) & 0xff; d0 = (-y + x) & 0xff
            was = pos
            if n == 10:
                acc = (acc + ((d0 + d1) & 0xff)) & 0xff
                acc = acc - 256 if acc > 127 else acc
                n = 0
                acc = min(acc, 32) if acc > 0 else max(acc, -32)
                if abs(acc) > 12:
                    c = name[pos]
                    if acc > 0:
                        acc -= 12; c += 1
                    else:
                        acc += 12; c -= 1
                    c = {9: 0x20, 0x21: 0x41, 0x5b: 8 if pos else 0x20, 7: 0x5a,
                         0x1f: 8 if pos else 0x5a, 0x40: 0x20}.get(c, c)
                    name[pos] = c
                    t.big(13 + 2 * pos, 6, chr(c), 0x1400)
            if start:
                if name[pos] == 8:
                    name[pos] = 0x20; pos -= 1
                else:
                    pos += 1
            if pos != was:
                t.big(13 + 2 * was, 6, chr(name[was]), 0x1000)
                if pos < 3:
                    t.big(13 + 2 * pos, 6, chr(name[pos]), 0x1400)
        name = [0x20 if c == 8 else c for c in name]
        initials = bytes(name).decode('latin1')
        table.insert(rank, score, initials)
        for row in (6, 7):
            for x in range(0x2a):
                t.put(x, row, 0)
        t.erase(0x22b76); t.erase(0x228ee)
        for row in (0x13 - 0x13, 1):                           # the clock's big digits
            for x in range(4):
                t.put(0x13 + x, row, 0)
        if players == 2:
            for x in range(16):
                t.put(12 + x, 4, 0)
        t.hs_table(table, -1, entry=True)
        return rank, initials

    def tick_timed(self):
        for item in list(self.timed):
            item[1] -= 1
            if item[1] <= 0:
                self.t.erase(item[0]); self.timed.remove(item)

    # -- racing -------------------------------------------------------------------------
    def which(self, i):
        return 0 if self.players == 1 else i + 1

    def race_start(self, race, timers, seconds, scores, active=None, winner=None):
        """winner: in a two-player game with both still in, the player first over the
        line last race (0x4003A4) - 'WON LAST RACE: + 5 sec' first (ROM 0x107C0): the
        5 counts down onto that player's clock a second every 20 frames."""
        t = self.t
        n = self.players
        active = active or [True] * n
        t.clear(); t.normal_colours(); self.timed = []
        t.frame_283c2(n); t.score_box_106(n)
        for i in range(n):                          # (0x106E2: every player in the game,
            t.score_28624(i, scores[i])             # out or not - 0x400396 stays at 2)
            t.timer_286ee(self.which(i), timers[i])
        self.race, self.timers, self.active = race, list(timers), active
        self.count = self.secs = seconds
        self.shown = (tuple(scores), tuple(timers))
        if winner is not None:
            self.winner, self.won_left = winner, 5
            self.won_attr = 0x2c00 if winner else 0x2800
            t.message(0x22b5e if winner else 0x22b46, self.won_attr)
            self.won_digit()
            self.phase, self.wait = 'won', 60
        else:
            self.winner = None
            self.start_banner()

    def won_digit(self):
        self.t.big_number(self.won_left, self.t.rom[0x1d362 + self.winner], 3, 1, self.won_attr, flags=2)

    def start_banner(self):
        if self.winner is not None:                 # (0x10900: the message erased, the
            self.t.erase(0x22b5e if self.winner else 0x22b46)   # side edges and timer box
            self.t.frame_283c2(self.players)                    # drawn again)
        self.t.race_intro(self.race, self.secs)
        self.phase, self.wait = 'banner', 60

    def intro_step(self):
        """One frame of the intro; returns the timers, or None once it is over."""
        if self.phase is None:
            return None
        self.wait -= 1
        if self.wait > 0:
            return self.timers
        if self.phase == 'won':
            if self.won_left > 0:
                self.won_left -= 1
                self.timers[self.winner] += 1
                self.won_digit()
                self.t.timer_286ee(self.which(self.winner), self.timers[self.winner])
                self.wait = 20 if self.won_left else 20 + 30
                return self.timers
            self.start_banner()
            return self.timers
        if self.phase == 'banner':
            self.phase = 'count'
        if self.phase == 'count':
            if self.count > 0:
                self.count -= 5
                self.t.big_number(max(self.count, 0), 0x1d, 9, 2, 0x3400)
                for i in range(self.players):
                    if self.active[i]:
                        self.timers[i] += 5
                        self.t.timer_286ee(self.which(i), self.timers[i])
                self.wait = 10
                return self.timers
            self.phase, self.wait = 'after', 60 - self.secs
            if self.wait > 0:
                return self.timers
        self.t.race_intro_end()
        self.phase = None
        return None

    def update(self, scores, timers):
        scores, timers = tuple(scores), tuple(timers)
        if (scores, timers) != self.shown:
            for i in range(self.players):
                if scores[i] != self.shown[0][i]:
                    self.t.score_28624(i, scores[i])
                if timers[i] != self.shown[1][i] and self.active[i]:
                    self.t.timer_286ee(self.which(i), timers[i])
            self.shown = (scores, timers)

    def bonus(self, player, amount):
        self.t.bonus(player, amount)

    def surface(self, pygame):
        key = (bytes(self.t.alpha), tuple(self.t.pal))
        if key != self.surface_key:
            img = self.t.image(self.chars)
            surf = pygame.image.frombuffer(img.tobytes(), (img.shape[1], img.shape[0]), 'RGBA')
            self.image, self.surface_key = surf.copy(), key
        return self.image
