"""Marble movement on the decoded Marble Madness height maps (no pygame needed).

World coordinates: (U, V) on the isometric lattice, 1.0 = one cell edge; Z in
height units (1 unit = 1 screen pixel, 0x4000 = reference level).

Projection (derived from the rasteriser's cell formula):
    x = 8*(V - U) + 136
    y = 4*(U + V) - 88 + y_offset - (Z - 0x4000)

These physics are a simple stand-in, NOT the arcade routine: the constants are
tuned by feel.  Swap in the real values once the marble-update routine is ported.
"""
import math
import numpy as np

CELL_PX = 11.3          # horizontal size of one cell edge, in height units (pixels)
GRAVITY = 0.35          # height units per frame^2
ROLL_ACCEL = 0.020      # cells per frame^2 per unit of input
FRICTION = 0.985        # velocity kept per frame while rolling
SLOPE_GAIN = 0.55       # how strongly slopes pull the marble
STEP_UP = 3.0           # tallest ledge the marble can climb, in pixels
LEAVE_GROUND = 2.5      # floor this far below the marble = airborne
FATAL_FALL = 56.0       # landing after a drop larger than this breaks the marble
MAX_SPEED = 0.45        # cells per frame
BOUNCE = 0.35           # fraction of speed kept when hitting a wall


def lattice_to_cell(u, v):
    """Cell (row, col) whose top corner is lattice point (u, v)."""
    r = u + v - 21
    return r, (r >> 1) + 21 - u


class HeightField:
    def __init__(self, grid, y_offset):
        self.g = grid                      # (rows, 22, 4) corner heights
        self.rows = grid.shape[0]
        self.y_offset = y_offset

    def corners(self, u, v):
        r, c = lattice_to_cell(u, v)
        if not (0 <= r < self.rows and 0 <= c < 22 - (r & 1)):
            return None
        cs = self.g[r, c]
        ok = cs > 0x2000                    # 0 = no floor, small values = special codes
        if not ok.any():
            return None
        top = cs[ok].max()
        return [float(x) if k else float(top) for x, k in zip(cs, ok)]

    def height(self, U, V):
        """Floor height under (U, V), or None over a hole."""
        u, v = math.floor(U), math.floor(V)
        cs = self.corners(u, v)
        if cs is None:
            return None
        fu, fv = U - u, V - v
        w0, w1, w2, w3 = cs                 # (u,v) (u,v+1) (u+1,v+1) (u+1,v)
        return (w0 * (1 - fu) * (1 - fv) + w1 * (1 - fu) * fv +
                w2 * fu * fv + w3 * fu * (1 - fv))

    def to_screen(self, U, V, Z):
        return 8 * (V - U) + 136, 4 * (U + V) - 88 + self.y_offset - (Z - 0x4000)

    def find_start(self):
        """Top-most spot near the middle with a 3x3 patch of flat floor around it."""
        best = None
        for r in range(self.rows):
            for c in range(3, 19):
                u = (r >> 1) + 21 - c
                v = r + 21 - u
                h0 = self.height(u + .5, v + .5)
                if h0 is None:
                    continue
                if any(self.height(u + .5 + du, v + .5 + dv) != h0
                       for du in (-1, 0, 1) for dv in (-1, 0, 1)):
                    continue
                x, y = self.to_screen(u + .5, v + .5, h0)
                if 40 < x < 248 and y > 8 and (best is None or y < best[0]):
                    best = (y, u + .5, v + .5)
        return best[1], best[2]


class Marble:
    def __init__(self, field):
        self.f = field
        self.reset()

    def reset(self):
        self.U, self.V = self.f.find_start()
        self.Z = self.f.height(self.U, self.V)
        self.vu = self.vv = self.vz = 0.0
        self.airborne = False
        self.fall_from = self.Z
        self.broken = 0                     # frames left in the "broken" state

    def step(self, ax=0.0, ay=0.0):
        """Advance one frame.  ax, ay = input in screen directions (-1..1)."""
        if self.broken:
            self.broken -= 1
            if self.broken == 0:
                self.reset()
            return
        # screen right = (+V, -U), screen down = (+U, +V)
        self.vu += ROLL_ACCEL * (ay - ax)
        self.vv += ROLL_ACCEL * (ay + ax)

        h = self.f.height(self.U, self.V)
        if not self.airborne and h is not None:
            # slope: finite-difference gradient of the floor
            e = 0.05
            hu = self.f.height(self.U + e, self.V); hv = self.f.height(self.U, self.V + e)
            if hu is not None and hv is not None:
                self.vu -= SLOPE_GAIN * GRAVITY * (hu - h) / e / CELL_PX / CELL_PX
                self.vv -= SLOPE_GAIN * GRAVITY * (hv - h) / e / CELL_PX / CELL_PX
            self.vu *= FRICTION; self.vv *= FRICTION
        sp = math.hypot(self.vu, self.vv)
        cap = getattr(self, 'max_speed', MAX_SPEED)
        if sp > cap:
            self.vu *= cap / sp; self.vv *= cap / sp

        nU, nV = self.U + self.vu, self.V + self.vv
        nh = self.f.height(nU, nV)
        if nh is not None and nh > self.Z + STEP_UP:
            # wall: bounce back along the blocked axis/axes
            if (self.f.height(nU, self.V) or -1e9) > self.Z + STEP_UP:
                self.vu = -self.vu * BOUNCE; nU = self.U
            if (self.f.height(self.U, nV) or -1e9) > self.Z + STEP_UP:
                self.vv = -self.vv * BOUNCE; nV = self.V
            if nU != self.U and nV != self.V:
                self.vu, self.vv = -self.vu * BOUNCE, -self.vv * BOUNCE
                nU, nV = self.U, self.V
            nh = self.f.height(nU, nV)
        self.U, self.V = nU, nV

        if self.airborne:
            self.vz -= GRAVITY
            self.Z += self.vz
            if nh is not None and self.Z <= nh:
                drop = self.fall_from - nh
                self.Z, self.vz, self.airborne = nh, 0.0, False
                if drop > FATAL_FALL:
                    self.broken = 60
            elif self.Z < 0x4000 - 600:
                self.broken = 1           # fell off the world
        else:
            if nh is None or nh < self.Z - LEAVE_GROUND:
                self.airborne, self.vz, self.fall_from = True, 0.0, self.Z
            else:
                self.Z = nh

    def screen_pos(self):
        return self.f.to_screen(self.U, self.V, self.Z)

    def shadow_pos(self):
        h = self.f.height(self.U, self.V)
        return None if h is None else self.f.to_screen(self.U, self.V, h)
