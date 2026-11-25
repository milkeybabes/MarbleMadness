"""Marble Madness sound: the arcade's sound commands played from recordings.

The arcade's main CPU sends a sound by writing a number (0x00-0x67) to the sound board
(FUN_158AC -> BIOS 0x4C6E).  The board's 6502 program (136033.421/.422) looks it up:

  9B3D[n]  command type: 6 start a sound, 4 stop one, 8 fade one, 0 set the volume
           threshold, 2 stop everything (n=0) / start-lamp settings, 1 a flag;
           3, 6, 7 are answered at once (status, ping)
  9BA5[n]  its parameter: for type 6 the sound's index s, for 4/8 the command whose
           sound to stop or fade, for 0 the threshold
  9C23[s]  the sound's first track; per track x: 9E03[x] the voice it plays on (0-3
           POKEY, 4-B the YM2151's eight), 9CB1[x] its priority, 9F55[x] the next track
  9C6A[s]  0: don't restart the sound while it is still playing

On each voice the tracks are kept in priority order (0x85ED): the highest is heard, a
lower one carries on silently underneath, an equal one replaces the old.  A sound whose
priority is below the threshold (0x8D48, set by command 1 to 0x3C, by 2 to 0) is muted.

Each sound here is a recording of the real board (MAME, mm_sounds.lua) playing it alone:
sounds/mm_NN.wav, or sounds/mm_NN.ogg for the long ones, whose loop points are in
sounds/long_sounds.json.  A sound is heard at the share of its voices it holds.
"""
import json
import os

from marble_tables import (SOUND_TYPES, SOUND_PARAMS, SOUND_TRACKS,
                           SOUND_RETRIG, SOUND_INDEX)

class SoundTables:
    """Sound command semantics in native Python; does not read 6502 ROMs."""
    def __init__(self, roms=None):
        self.type = list(SOUND_TYPES)
        self.param = list(SOUND_PARAMS)
        self.tracks = {int(k): dict(v) for k,v in SOUND_TRACKS.items()}
        self.retrig = dict(SOUND_RETRIG)
        self.index = dict(SOUND_INDEX)


# Music that waits for the game: a group's flag (command type 1: 0x12 sets group 1's,
# 0x13 clears it) is tested by the sequencer's opcode 0x92D5 at fixed points, and when it
# is set the music skips on to its next part.  0x10 (Aerial, from zone 4) tests it 98
# frames after it starts and every 97 frames after that (MAME debugger, 0x92D5 hits);
# the part after the skip is a recording of its own.
BRANCH = {0x10: dict(group=1, first=98, every=97, part='10b')}


class _Playing:
    def __init__(self, n, tracks, chan, loop):
        self.n, self.tracks, self.chan, self.loop = n, dict(tracks), chan, loop
        self.order = 0
        self.fading = False
        self.sound = None
        self.vol = None
        self.start = 0
        self.branched = False


class Sounds:
    FADE_MS = 1000                       # a fade (type 8): the board's rate isn't measured

    def __init__(self, roms, folder, volume=1.0):
        import pygame
        self.pg = pygame
        self.t = SoundTables(roms)
        self.folder = folder
        self.volume = volume
        self.threshold = 0
        self.flags = [0] * 8                 # the sound groups' flags ($0004-$000B)
        self.frame = 0
        self.playing = []
        self.count = 0
        self.cache = {}
        meta = os.path.join(folder, 'long_sounds.json')
        self.long = json.load(open(meta)) if os.path.exists(meta) else {}
        self.ok = pygame.mixer.get_init() is not None
        self.free = []
        if self.ok:                              # our own channels: each sound keeps its
            pygame.mixer.set_num_channels(32)    # channel until it is over
            self.free = [pygame.mixer.Channel(i) for i in range(32)]

    # ---- files --------------------------------------------------------------
    def _load(self, n):
        if n in self.cache:
            return self.cache[n]
        key, pg = (n if isinstance(n, str) else '%02x' % n), self.pg
        got = None
        try:
            if key in self.long:
                info = self.long[key]
                whole = pg.mixer.Sound(os.path.join(self.folder, info['file']))
                ls = info.get('loop_start')
                if ls is None:
                    got = (whole, None)
                else:                        # the whole recording once, then from the
                    raw = whole.get_raw()        # loop point round and round
                    fr = pg.mixer.get_init()
                    step = abs(fr[1]) // 8 * fr[2]
                    cut = int(ls * fr[0]) * step
                    got = (whole, pg.mixer.Sound(buffer=raw[cut:]))
            else:
                p = os.path.join(self.folder, 'mm_%s.wav' % key)
                if os.path.exists(p):
                    got = (pg.mixer.Sound(p), None)
        except Exception as e:
            print(f'sound {key}: {e}')
            got = None
        if got is None and n != 0x3b:                  # (0x3B is silent on the board)
            print(f'sound {key}: no mm_{key}.wav / .ogg in {self.folder}')
        self.cache[n] = got
        return got

    # ---- the board's commands -----------------------------------------------
    def send(self, n):
        if not self.ok or not 0 <= n < 0x68:
            return
        t, p = self.t.type[n], self.t.param[n]
        if t == 6:
            self._start(n)
        elif t == 4:
            self._stop(lambda s: s.n == p)
        elif t == 8:
            for s in [s for s in self.playing if s.n == p and not s.fading]:
                s.fading = True
                s.chan.fadeout(self.FADE_MS)
        elif t == 0:
            self.threshold = p
        elif t == 2 and p == 0:
            self.stop_all()
        elif t == 1:                                 # a group's flag (0x8375)
            self.flags[p & 7] = p & 0x80
        # (type 1, the lamp settings 0x61-0x67 and 3/6/7 make no sound)

    def stop_all(self):
        self._stop(lambda s: True)

    def _stop(self, which):
        for s in [s for s in self.playing if which(s)]:
            self._end(s)

    def _start(self, n):
        if not self.t.retrig[n] and any(s.n == n for s in self.playing):
            return                               # 0x84F4: already playing, left alone
        snd = self._load(n)
        if snd is None:
            return
        new = self.t.tracks[n]
        for s in list(self.playing):             # an equal priority replaces (0x8607)
            for v, pr in new.items():
                if s.tracks.get(v) == pr:
                    del s.tracks[v]
            if not s.tracks:
                self._end(s)
        if not self.free:                        # all 32 in use: the oldest makes way
            self._end(min(self.playing, key=lambda s: s.order))
        chan = self.free.pop()
        chan.set_volume(1.0)
        chan.play(snd[0])
        s = _Playing(n, new, chan, snd[1])
        s.sound = snd[0]
        s.start = self.frame
        self.count += 1
        s.order = self.count
        self.playing.append(s)
        self._levels()

    # ---- once a frame -------------------------------------------------------
    def update(self):
        if not self.ok:
            return
        # (no Channel.queue: pygame starts a queued sound whenever the channel stops,
        # a stop or a fade-out included, so a stopped loop would carry on)
        self.frame += 1
        for s in list(self.playing):
            b = BRANCH.get(s.n)
            if b and not s.branched and not s.fading and self.flags[b['group']]:
                k = self.frame - s.start - b['first']
                if k >= 0 and k % b['every'] == 0:        # a test with the flag set: on
                    part = self._load(b['part'])
                    if part is not None:
                        s.chan.play(part[0])
                        s.sound, s.loop, s.vol = part[0], part[1], None
                    s.branched = True
            if s.chan.get_busy():
                continue
            if s.loop is not None and not s.fading:      # the recording ran out: loop
                s.chan.play(s.loop, loops=-1)
                s.sound = s.loop
                s.vol = None
            else:
                self._end(s)
        self._levels()

    def _end(self, s):
        s.chan.stop()
        if s in self.playing:
            self.playing.remove(s)
        self.free.append(s.chan)

    def _levels(self):
        top = {}                                 # voice -> (priority, newest) heard
        for s in self.playing:
            for v, pr in s.tracks.items():
                k = (pr, s.order)
                if v not in top or k > top[v]:
                    top[v] = k
        for s in self.playing:
            held = sum(1 for v, pr in s.tracks.items() if top[v] == (pr, s.order))
            share = held / len(s.tracks) if s.tracks else 0
            if max(s.tracks.values(), default=0) < self.threshold:
                share = 0
            v = share * self.volume
            if v != s.vol and not s.fading:          # (left alone while pygame fades it)
                s.chan.set_volume(v)
                s.vol = v
