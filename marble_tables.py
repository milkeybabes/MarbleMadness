#!/usr/bin/env python3
"""Marble Madness native gameplay tables (no ROM files, no ROM loading).

This is an interim address-indexed adapter for the translated routines in
marble_engine.py and marble_text.py. It contains only selected DATA TABLES,
message records, paths, and frame scripts derived from user-supplied reference
files, not the executable 68010 or 6502 programs. The address indices exist
because the Python port has not yet given every table a symbolic name.

Animation and gameplay table data now spans 0x1D000-0x251DD inclusive,
covering the previous 0x21300 gap. The block ends immediately before 68010
instructions at 0x251DE. Other unmapped reads still raise immediately,
avoiding silently replacing missing assets with zeroes.
The seven named LEVEL2_DYNAMIC_CELL_FRAMES repair the Level 2 animated
collision patches at 0x1B908-0x1B9CB (no machine code included).
The named sprite-layout data covers all motion-object frame layouts
from 0x29014 through 0x29CCD, before the 68010 routine at 0x29CCE.
"""
from __future__ import annotations
import base64
import zlib
from functools import lru_cache

# Four Ultimate Race height-cell collision patches, each containing 112
# 16-bit values.  The existing 8-byte frame descriptors refer to these
# records, in order.  The final record ends exactly at 0x251DE, where
# executable 68010 instructions BEGIN (not included).
ULTIMATE_CELL_PATCH_FRAMES = (0x24E2A, 0x24E32, 0x24E3A, 0x24E42)
ULTIMATE_CELL_PATCH_RECORDS = (0x24E4E, 0x24F32, 0x25016, 0x250FA)
ULTIMATE_CELL_PATCH_COUNT = 112
ULTIMATE_CELL_PATCH_END = 0x251DE

# Level 2's animated terrain changes the collision-height cell table as its
# playfield animation cycles.  A frame updates 12 consecutive dynamic cells.
# These are SEMANTIC VALUES (start cell, 12 collision codes), not executable
# machine instructions. The original scripts still refer to frame addresses,
# so the adapter below exposes the records at their former lookup positions.
#
# The seven records are the ONLY data in this section: the preceding 68010
# routine ends at 0x1B906, and executable instructions resume at 0x1B9CC.
LEVEL2_DYNAMIC_CELL_START = 0x1B908
LEVEL2_DYNAMIC_CELL_STRIDE = 28
LEVEL2_DYNAMIC_CELL_FRAMES = (
    (0, (0xEFC8, 0xF048, 0x6FC8, 0xF048, 0x6FC8, 0xF048, 0x6FC8, 0x9A48, 0xF048, 0x9A48, 0x9A48, 0x9A48)),
    (0, (0x0070, 0x314B, 0x6FCA, 0xF04A, 0x6FC9, 0xF049, 0x6FC9, 0x0074, 0xF049, 0x0034, 0x0038, 0x0038)),
    (0, (0x002C, 0x324D, 0x6FCC, 0xF04C, 0x6FCA, 0xF04A, 0x6FC9, 0x0030, 0xF049, 0x9ACC, 0x0034, 0x0038)),
    (0, (0x0078, 0x33D0, 0x6FCE, 0xF04E, 0x6FCC, 0xF04C, 0x6FCA, 0x007C, 0xF04A, 0x0080, 0x9ACC, 0x0034)),
    (0, (0x003C, 0x3452, 0x6FD0, 0xF050, 0x6FCD, 0xF04D, 0x6FCB, 0x0040, 0xF04B, 0x9B50, 0x0044, 0x0048)),
    (0, (0x0084, 0x0088, 0x6FD2, 0xF052, 0x6FCF, 0xF04F, 0x6FCC, 0x008C, 0xF04C, 0x0090, 0x0094, 0x9ACC)),
    (0, (0x004C, 0x35D8, 0x6FD4, 0xF054, 0x6FD0, 0xF050, 0x6FCC, 0x0050, 0xF04C, 0x9BD4, 0x9B50, 0x9ACC)),
)
LEVEL2_DYNAMIC_CELL_END = LEVEL2_DYNAMIC_CELL_START + len(LEVEL2_DYNAMIC_CELL_FRAMES) * LEVEL2_DYNAMIC_CELL_STRIDE

@lru_cache(maxsize=1)
def _level2_cell_data():
    # A small compatibility encoding. In the final native refactor the engine
    # can consume LEVEL2_DYNAMIC_CELL_FRAMES directly without address lookups.
    from struct import pack
    chunks = []
    for first, codes in LEVEL2_DYNAMIC_CELL_FRAMES:
        assert len(codes) == 12
        chunks.append(pack('>hH12H', first, len(codes), *codes))
    blob = b''.join(chunks)
    assert len(blob) == LEVEL2_DYNAMIC_CELL_END - LEVEL2_DYNAMIC_CELL_START
    return blob

# Native sprite composition records for the animated scenery, hazards, goals,
# and frame objects. These are DATA (column counts, offsets, sprite indices).
# Source frame-script entries have a pointer at +8 into this block, sometimes
# with the low bit set to request horizontal mirroring. The 68010 routines at
# 0x29000 and 0x29CCE are deliberately not included.
# The offset is preserved temporarily for the translated sprite-list decoder.
SPRITE_LAYOUT_START = 0x29014
SPRITE_LAYOUT_END = 0x29CCE  # exclusive
_SPRITE_LAYOUT_PACKED = (
    'c-oCtdyH4b6+Vx-Gr#A)*j>;Py6J;f#Yc^;4N*%PgSAi;0hJbY>w{|1q7)GY%JP16>qZG`rO1XV2!g9npn&KGETY{Y2%@6WjUmJm'
    'T8gbLvb(?A?;dXZ7c3CI^PO|%n=^Cf&b|9%O8yBk;lIYQgHHp2Yk|QW(RFEVC<_e--w2GfTuh@ds~&tia95hU8@Vyy`#8}3>D+P5'
    '^dR^l)OaM#O-8N(Je?iQN^;yubEK2z0gXVDRePKk(o?j^#sfWrInBr}#lD_PbIY;b3g89yv|6(8TIgltwY1K718p?kOk3huJ;&4v'
    'Y$c`J5Tgi9#oDg}+bt$~4IA9SC+N*YPbK;@{5I%zB8us4+C{r9{|@b;y;l2OdJo#bCHesP5Vb$XN=yf+ojyrdJ&3Lj0e{s9JshE<'
    '^jR_@UGzC{9Q8XShuNJ}!t*F{HP><k?j_1)d@lEnV`0v7AIWf?I0^h!?u%T8EBHLB<O||B@|5L%$XuuqUm|(F47<BL$+P4B@UOs5'
    '27s^TD!#_@S-uwWAay(htKWdRBXF`A#5Zf6M`?lUxt#AzPR2PN4UEw&<H<%5*7_bRkCzfp03HS=W3HzhPo*L^VDHnY2hTvxQHEzy'
    'o@XI9n=5%Pay8iZe5}{V1#UuLkEOW<$Sp+t)c<nSi=W1R7XgcbX6&U#s(7iE^K;UZS0FBvKD<hMa*OuhwTO#|Hz3}0GM40q<Eitj'
    '=56EcY3@xHZcF{0%)C2}GbBacgC6&}i1$a#?OMr)q=pakIs7;3%}Y#E&PQ=RpSi&Qj+~GyaEDfimmWC9S}B^QO3JabDwmVmC`g?v'
    'moI6x^pzSppL@!MiH3X2#hCXkSCDT<8R;M8WPq!XfznF`;mHrNx203AXO&^V2rBSjlAP<3o7BlDDM~$XhvhnDw2F+8yo{BCj7xG<'
    'kOwhu0x%JEPQHCbnF2Im{tVQaW4VGfVqO#GKdvlKF!2}iG%U&QfG%l<jpg^ro~c5XaY3Guj4VfumAKC@V%{2bzn*ilF^;o|W-~Oe'
    'K+_7%Hmvi8s<a{A#d&#^in7OawX&Bh<z4LOJ<ZB~%}cu$<RI{=l*<vSk)x)ok$*sU0J>wEk$*Xr<IXE-IaaU2`%|k~)Ai6CSD{Lo'
    '7B#08&{RjF=eWGqA^tiS^}IOF5moD#rKlHZRxfgqUK+7p9z{AJ@;cB39n6(Fl!`jcYF6uT)VosiIs#)OBh`8z>*y$>_qnY8Qt~>{'
    'YF6naDd=SA8lamA-8|@;JnIvv-K-g1#yP#!YMzDG&iV|xI1b%v;@F>d9PjWc$E=qMoyrxu66doD=kpTIXPxiTR!{my6m%!faxWM3'
    '{gco7%-WOprhA?B(v8M?InB8Ba*8qQrA+r@uODjAcBi^IiuIuH(8D-`V_5gNi*WygF1B9ILTkO8L1(>mqp@C2Gj6?{V$6D}bP=A{'
    '$C`B>d&x!Y%6*rs@g-O1g8PPKT|dgZi>;Tl&{{8N&{;3tXsnmhj9V|K7?V7aTkf96_n^~V>5^}_;w!oeXu-%Di(|JISWmIrXn*aU'
    'mJn|Owy<}t_I*>gmAl+FIpO}q;&%K$S8{KmavQ6A8?_>6c1y|a0rp8&I@|~DBm3^3@VBt|tDs5zdw_eH{Ma;p02t3*{-NX-l7HAw'
    'GM?fajHmmVi4NXuU@noLn#S{iMq>Y{>6-jw#tZyH<0t)ZjTiaF#?5|dI`3Iv8I}B!G=3gfiMp#yx7xQDzwFl<uk#y>H~P)STYRhW'
    'R{y&3cE7{;E#H>b+X?KV$iHUMJASX<XWsjMzyHX*1HRpVV%|Z2$bXv7I}9AbzW#3Yj{0N9|MZ_5|Hpq}+>vruuwRrxf=gxxA2K0l'
    'ULll)iqxwL)k$?yA0w^>dfCXip?BzGUS0S~_?mfr!#BhE=Jg8~g-gu4G+Y+GW8QZ||8RwQSA_xL`{rF828JKtml=g0njIvG9UOjS'
    'JS1Fa+3U>?HG6~EVP=P$9bxuHvp1Q&+3ZNOKQ{Xlv!l%3V)j<Ex0$Us`%_!<_Hc(qcbdJ+>}az;v+=vbJ$7n85BFO37iPzrz2EEu'
    'Ha;$lH=YnCCN~)QM-WXiKMqsi|H`rrVVd#uFvECe8qZ4O*(uLSd2Y({Ql6jkuTySJd0NW9N%_%)v1(JWTOJk!yXE1@V7ELh3U<pw'
    'b6A?}5#F=-+%B>Ga#)6Vxn);`7m$&#I=q;UwIKVFjjaiPFkT=2XuK)B@?UpTzc>'
)

@lru_cache(maxsize=1)
def _sprite_layout_bytes():
    blob = zlib.decompress(base64.b85decode(_SPRITE_LAYOUT_PACKED))
    assert len(blob) == SPRITE_LAYOUT_END - SPRITE_LAYOUT_START
    return blob

# Ultimate Race (race 6) has four column-wise occlusion maps. Each map is
# 36 screen columns x 26 bytes (200 height-map rows plus a terminal byte).
# The moving course switches among these four maps via 0x40045C in
# level_object_update_13068; FUN_264AA reads bitmap bits to determine whether
# the marble is hidden behind a wall. These are binary visibility maps, not
# 68010 instructions. The subsequent executable routine starts at 0x2FF10.
ULTIMATE_OCCLUSION_START = 0x2F070
ULTIMATE_OCCLUSION_PHASES = 4
ULTIMATE_OCCLUSION_COLUMNS = 36
ULTIMATE_OCCLUSION_COLUMN_BYTES = 26
ULTIMATE_OCCLUSION_END = (ULTIMATE_OCCLUSION_START +
                          ULTIMATE_OCCLUSION_PHASES * ULTIMATE_OCCLUSION_COLUMNS *
                          ULTIMATE_OCCLUSION_COLUMN_BYTES)  # 0x2FF10, exclusive
_ULTIMATE_OCCLUSION_PACKED = (
        'c-rNbO^PEi425Omg_aJ?0hC(xF+|A09Oy-kF-M*EEQblSH83GC4O39vDL-4be!fzPnDDybR*9?N9=Q8_x=EF`l)98T^DgBvKBSdU=Ezm*eT3qo'
        'HIy0#YA>gdw6{=7Y+9rqp)?6)+87=Gnm>$$HJ`T4vGIDs`a)&8V;CtFW5(GdOig$CSdMfq8^fv4ouP4##wRQ*XDSW9yv5m-r^2#YsZ=P3)ah^M'
        '1T1TfO2M3ylo=y~(ayu)mogB~&W&(%R~qRhtT}REGr*25X+&yxZeolG=AL~8pI+nc(iP7<l&|1pbinhj9b(g!ks8+Auv0S_)V7p!D2Q0Q)B{^{'
        '?m9;7F+^{2T8ZNHF-w5Em(gw<W`NxhDCB?(;I`|9cCPf)U1!J*C-^D$!|3SY5gj_Gh{nK?KRSa`#*C=ZxfxOT@&R>*kV7t=6J~y3tuc5^h02(z'
        'n+n3~m3yqT6<;NVSqu`#BGqvJ`lt|=AyYTY11rlRtEyOfOt&o~bya*^SFtEcto)Q`<t}|a8Gb)2a(WQwvnHOD<2P9nC-}<gkc?B8gwrsx<5U>m'
        'j8lCtr;>3hDW{%tdbX6)zU353(=DesjqW)GH+|2kyuzuboc5Gc#;fc&6_40`<&=f!vgH)TYkN-dcC$UFc)R(YQ`N}cmQ(q*oQ{-JPdPQoI5m4K'
        'rzPd|m3}HIr}DS`6lqWTDH7ze;}lj|kQ+|9Q>`WIr=M9r)sgj67W=>MrzPvBdrrqlIW2$Uw5ObE%IPcpv}T+xYhO5hykR$-YRYMQp`U8rPromx'
        'J>}H0eyX1JQ!J?2cP|6C@28UYQ%n14PrrLL{q9vwIem2J4W~WjRR42MCFOJ$(w5WlQa`<<{q&Y|THeX&{QC=_2^GH'
    )

@lru_cache(maxsize=1)
def _ultimate_occlusion_blob():
    blob = zlib.decompress(base64.b85decode(_ULTIMATE_OCCLUSION_PACKED))
    assert len(blob) == ULTIMATE_OCCLUSION_END - ULTIMATE_OCCLUSION_START
    return blob

def ultimate_occlusion_column(phase: int, column: int) -> bytes:
    """Get one decoded 26-byte visibility bitmap without any memory addressing."""
    if not (0 <= phase < ULTIMATE_OCCLUSION_PHASES and
            0 <= column < ULTIMATE_OCCLUSION_COLUMNS):
        raise IndexError((phase, column))
    i = (phase * ULTIMATE_OCCLUSION_COLUMNS + column) * ULTIMATE_OCCLUSION_COLUMN_BYTES
    return _ultimate_occlusion_blob()[i:i + ULTIMATE_OCCLUSION_COLUMN_BYTES]

# Native sound-board command dispatch, no 6502 ROM, no machine execution.
SOUND_TYPES = (2, 0, 0, 255, 6, 6, 255, 255, 6, 8, 6, 8, 6, 8, 6, 8, 6, 8, 1, 1, 6, 8, 6, 8, 6, 8, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 4, 6, 6, 6, 6, 6, 4, 6, 6, 6, 4, 6, 4, 6, 4, 6, 4, 6, 4, 6, 4, 6, 4, 6, 4, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 2, 2, 2, 2, 2, 2, 2)
SOUND_PARAMS = (0, 60, 0, 0, 0, 1, 0, 0, 2, 8, 3, 10, 4, 12, 5, 14, 6, 16, 129, 1, 7, 20, 8, 22, 9, 24, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 57, 42, 43, 44, 45, 46, 63, 47, 48, 49, 67, 50, 69, 51, 71, 52, 73, 53, 75, 54, 77, 55, 79, 56, 81, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 1, 2, 3, 4, 5, 6, 7)
SOUND_TRACKS = {4: {4: 8, 5: 8, 6: 8, 7: 8, 8: 8, 9: 8, 10: 8, 11: 8}, 5: {0: 8, 1: 8, 2: 8, 3: 8}, 8: {4: 6, 5: 6, 6: 6, 7: 6}, 10: {4: 6, 5: 6, 6: 6, 7: 6}, 12: {6: 8, 5: 8, 7: 8, 8: 8, 9: 8, 11: 8, 10: 8, 4: 8}, 14: {4: 6, 5: 6, 6: 6, 7: 6, 8: 6}, 16: {4: 6, 5: 6, 6: 6, 7: 6, 8: 6, 9: 6}, 20: {5: 5, 9: 5, 6: 5, 7: 5, 8: 5, 4: 5, 10: 5}, 22: {4: 8, 5: 8, 6: 8, 7: 8, 8: 8, 9: 8, 10: 8}, 24: {6: 8, 7: 8, 9: 8, 11: 8, 10: 8, 8: 8, 4: 8, 5: 8}, 26: {4: 62}, 27: {4: 62, 5: 62, 6: 62, 7: 62, 8: 62, 9: 62, 10: 62, 11: 62}, 28: {4: 61, 5: 61, 6: 61, 7: 61}, 29: {4: 61, 5: 61, 6: 61, 7: 61, 8: 61, 9: 61, 10: 61, 11: 61}, 30: {6: 61, 7: 61, 9: 61, 11: 61, 10: 61, 8: 61, 4: 61, 5: 61}, 31: {9: 7}, 32: {10: 7}, 33: {11: 7}, 34: {9: 7}, 35: {10: 7}, 36: {11: 7}, 37: {9: 7}, 38: {10: 7}, 39: {9: 7}, 40: {10: 7}, 41: {11: 7}, 42: {9: 7}, 43: {10: 7}, 44: {11: 7}, 45: {9: 7}, 46: {10: 7}, 47: {9: 13}, 48: {9: 13}, 49: {9: 8}, 50: {9: 7}, 51: {9: 7}, 52: {10: 9}, 53: {9: 10, 10: 10, 11: 10}, 54: {9: 10, 10: 10, 11: 10}, 55: {10: 7}, 56: {10: 9}, 57: {9: 5, 10: 5}, 59: {9: 6}, 60: {9: 11, 10: 11, 11: 11}, 61: {9: 11, 10: 11, 11: 11}, 62: {9: 11, 10: 11, 11: 11}, 63: {11: 14, 10: 14, 9: 14, 8: 14, 7: 14, 6: 14, 5: 14, 4: 14}, 65: {11: 63}, 66: {9: 5, 10: 5, 11: 5}, 67: {9: 12, 10: 12}, 69: {9: 9, 10: 9}, 71: {4: 7}, 73: {5: 7}, 75: {6: 7}, 77: {7: 7}, 79: {8: 7}, 81: {9: 7}, 83: {10: 12, 11: 12}, 84: {11: 6}, 85: {10: 6}, 86: {9: 6}, 87: {8: 6}, 88: {7: 6}, 89: {6: 6}, 90: {9: 9}, 91: {10: 9}, 92: {11: 9}, 93: {0: 8}, 94: {3: 8}, 95: {1: 8}, 96: {2: 8}}
SOUND_RETRIG = {4: False, 5: False, 8: False, 10: False, 12: False, 14: True, 16: True, 20: False, 22: False, 24: False, 26: True, 27: True, 28: True, 29: True, 30: False, 31: True, 32: True, 33: True, 34: True, 35: True, 36: True, 37: True, 38: True, 39: True, 40: True, 41: True, 42: True, 43: True, 44: True, 45: True, 46: True, 47: False, 48: False, 49: True, 50: True, 51: True, 52: True, 53: False, 54: False, 55: True, 56: True, 57: True, 59: False, 60: True, 61: True, 62: True, 63: True, 65: True, 66: True, 67: True, 69: True, 71: True, 73: True, 75: True, 77: True, 79: True, 81: True, 83: True, 84: True, 85: True, 86: True, 87: True, 88: True, 89: True, 90: True, 91: True, 92: True, 93: True, 94: True, 95: True, 96: True}
SOUND_INDEX = {4: 0, 5: 1, 8: 2, 10: 3, 12: 4, 14: 5, 16: 6, 20: 7, 22: 8, 24: 9, 26: 10, 27: 11, 28: 12, 29: 13, 30: 14, 31: 15, 32: 16, 33: 17, 34: 18, 35: 19, 36: 20, 37: 21, 38: 22, 39: 23, 40: 24, 41: 25, 42: 26, 43: 27, 44: 28, 45: 29, 46: 30, 47: 31, 48: 32, 49: 33, 50: 34, 51: 35, 52: 36, 53: 37, 54: 38, 55: 39, 56: 40, 57: 41, 59: 42, 60: 43, 61: 44, 62: 45, 63: 46, 65: 47, 66: 48, 67: 49, 69: 50, 71: 51, 73: 52, 75: 53, 77: 54, 79: 55, 81: 56, 83: 57, 84: 58, 85: 59, 86: 60, 87: 61, 88: 62, 89: 63, 90: 64, 91: 65, 92: 66, 93: 67, 94: 68, 95: 69, 96: 70}

# Unused flat profile padding after bank 1's last standard shape records.
# The bytes 0x83400..0x83F0D were checked against the source data and are
# literally all zero. The Ultimate visibility routine reaches two of these
# entries in its second moving-course configuration (0x83463/0x83464).
# This explicit verified data range is NOT a fallback for missing tables.
BANK1_ZERO_PROFILE_START = 0x83400
BANK1_ZERO_PROFILE_END = 0x83F0E  # first non-zero byte

# Address-indexed *table* fragments. Executable program ROM is omitted.
_DATA_BLOCKS = (
    (0x0, 0x80, (
        'c-mudkY`|EU`b<OU}m5p76D>4AhrNvFQ`}!hz0<KU;<1'
    )),
    (0xc00, 0x1100, (
        'c-nQ6QEXFH7{`BYXRWiYRdG%nuFS61Ik!VQ=Z16Z?l!FxOQ};)(N%?YOxN8tGtQ(5RTBcfKnNzju_vEP^hHC$9P&Ph4?c<EMMFsJ'
        '10m5g;{Wzm!i%~2opZk5ch7gfd+*^Up7~myTmL}uXK@3Tw~0Y9D^|s6@tsh9S$SJ|TX|b~TX|b~TX|b~`>Lo3)msUQ?IJFQ#FWU1'
        'yeNvY_)>f=z7eXivMIL2uj0D+8%hDuE}|kKQX(S`h()m`j{f!u;K7TJ7Vg5&-2}LYR_-OpeY6oG%>6vTgKXm=+Ig4`9$`C=(#Z}U'
        'ql*YT>E>~w^bq3-;yg(&Pthm;H2tbSp!$YgdQL*m=_4sW*qonf=7$tNt9Y0ZcB_x)80C4!7?)4UPiViT{gX^F%?rFpnwOa2<=g$c'
        '&)d_CUunh@WY|lVedKtx@qW9Q<uzXC4fgXUZ}Bz<c!zhHBhP!hulWNGa!BttPk{vvv&e^hq<-A1sLPp`G%sVbLP_hTh8b<<G)Br)'
        '!PKwZ5}LPaBGj+}Q$H_zUbZS*b#2(x&&ZyUJt=$AwH>B@MD~cREo-|rV(N3UbFx|4tZU<@J}x^hYh;aUjj4CZcFBfi!>%1R^^j~x'
        'mYO+7DD!dp_c3CltQ)K;Yud7mH@&e{iKH%FFfAK(X1jIr4#yMT%M+J3%-JwG%kHgO#b5e-{-gTpm6$&_ZMRmZi$CS2Y+rS%_=8(*'
        'wdIaV=OWI&nD$+BvSXG1%kH^C+3CrU7|{t}(Brjgcqi};d5nyw{B0`1=4A4Mvvz8vf2@3_T|sBp@Mv^^NK_s7lwxJ=Z;NXkTAcY*'
        '>-PEG10y;mt7%1^ROWlwvDz@@hFQ47tToK6Yxe$0hvmd&je6(I=^<u5F9ZWVnD;wT{)TgBcfT`s9&3!(bgxcAQh!0SH>v-rxh1JH'
        'pxNKZ1{z~)bW~3?7mo+`W&XN&eBR${WxmRH%C3VoX?)G^v}wM`O$H~!EL~5pMNo$v>d%~-ar!M~PZT<uZyEkCdvHB-DtjW|c01nh'
        '#7=iT>6uF5rqj7o-CKbg1!H`r;MIi$#<P{uX<^|HW8jigHQ=~^0ht9x8v'
    )),
    (0x7200, 0x7800, (
        'c-qBOzi-n(6n;q?(-tV9lm<$GD?bV?KLp{&z<>-@w??1@QHjmqqp>J<W#8GE5JH_88JHLt84xowVq)SCU}8cH3<$B7@9fLfq_H|7'
        'F8Xx-?tSm>-Fxpsq{b4}#)!g6>am?*#|+yAcEPaSVAl;B2U{|157<4!s$d<%_JYNRO@OT%wh!z@kI@mD!|y2B8yi$28;ch3l(ruY'
        '`nTgGa2l8e=K5^s2k6i0`AlxD1eAdxU>K<A`|SWmfKgx`IMuh;2|c4yg!&MwLZ}6y0))I1vQ5Y_A+v;hlB(u@U<#-K2Y`dXG;j#0'
        '1BXdvn~-ZF2Fw7e?#Bn*<=lThpUUtM@3Zflj{k_Y6Hh$ou*EP8LaI)YU3#LGI8%I8Nfw`4&SS=vf;r+E&KRxbbcjoirMjQXVIVjJ'
        'lR5jivd=5KoM>^!RsLnfEOq~jfVG{Nvo;sP^H<m`TkLuQWvpWlJN0X*sHLBN-@P63C{lai{_>gGqs1c;gk~G~Ps9IC>JyidFZnM0'
        'e%bRMK@Z6fng2rI`Rvx3@B%+VGogFrmz<>;xaw9GswRJB>layS!4z^A;VN5P)ek7OMVrRVs(bs=V5@%rm$z7P<~P{U<uR%n+x@(P'
        '9*Uo5-YsvXBUqE8piQ|dytP%2hh(9{;<tary%0!WM9fJK!a}C`T%gfGQKtG`pt_P*CG`T;)t(AFy#_u4-=VMe^9tTWa$o<S@rPTl'
        'MHYOL^=<B@G;aztjm=n+F7T>o9>RM}e&|;d_%dQQTo)k><BfK$KLOs+GX'
    )),
    (0x16580, 0x16b00, (
        'c-ozm%Wo4`6vn^t=SIegT0<Q$G^Hj@(g4A6Toas-5NEKRi4%sz;bGpxBV!W4c2j5@Fp^;bLPD|>S*QyZEUT)EDC(wN84Cdto3wub'
        'NFc-#FIYw~cP25IWPxBt_k8D`bMN`x(deG(QNL5$SQ|oCclAsmSaZF+kS@=J6Lw)E39IjGzcc;COkWbP36)ckSvh5^jwBsk$(@0v'
        '^S09qDAzTI*RnWw81t+jqS8PDsF^LMY(BXsSzaj5$5>0)bS(UCX6t>P^1O22yGkicXQS6r0k<^icyaH)KjG9`>qIqlG-b-=dxBZJ'
        '1#1vbtQG1O`lT`jAgulaDgIYHH}tH2UOlc}f}|H9jo*co=n^_B^lzb$A)WOIeJb=1p>M=!fHYw*i>Knh&sEk|_Eu^w);9LoYtV{q'
        '@R*|wUbMq_{Me2T>_7mW=)z8PqX$9sLB?)`5Jm+3P%wZ&3}Fvc?8Puf5HqWcVxJj1ieqM#!#HB@IEVA-HLF~}I3_-g%yo2e{W7va'
        '4RHg%Sw?=sdVc#kWNzelE68qcVgtXgCbN-EY~~Ma;pS>Gw{R<4Ka0GL9=6f@0lA$%{zyN!v*R6k2LtS67k9FoJyqmg>}Bv{WcE>B'
        'LEg;}!;G+>$|9Kqj0%R2gB;=>s@#h~4s(Ptj&dLO^8gPrZjgD1hk1lYd5p(DLOwxl1^FaT@ifnHjQSEe!LuCa1kVZbd0t?WDW>@o'
        'C;2lk3i2iX!e7n*6XI{2;$>c8<{i18SF6c_vZ@qZ{u)tt(eO0W28)$wL)h?6nN|*0qK#3*J7d~Vv=VJn+aHvIV`?c>3WnAD50;{H'
        'zK7o8*jy=6lEZVwhlQf@syEOq?*EOMmW*-3o4dtV-f7H;Q@UiUY|$D-ACTY@{lzXk!lhedR(I56uem8IK5y)gSsWH6`{>rp$fH~O'
        '|52-MO+&LgTCiTpDur0K4Wf5I`ZsEg=kx=UoE2S=dEIIDm9NyisjJoea83(MT0h9HTguj%*$v)KBc~KzW<~##UQfHzHYmetKAtmI'
        '^{V-$b6es$4bsKB2_)YFTDUk1'
    )),
    (0x1d000, 0x251de, (
        'c-rlK34Byln(udSt=TL4LYAA5ecyMIs-!9#NmWR~+6ZZc7-$4UpwUL99kju=MbV}qI^v*@rfElbw&SSn16<mL#=!V!29Z|Up&7LuED<n-'
        'r0)Blb8l5H0R+3}Ex+HJD*pHU&v(D?JLi07y%kb<?TqY6r@~AUQ?f~a=EtwSmdSE@<jgFWLX!UQ-yQz($+g8)m@j)?dri^y>TwO-Bby}s'
        'BfCg13Amb<S^i~z+Y<TgEi}4h8MS|0JK~(<T-oyPQf+Yo#iiDg*#2hb8WKA!%L4b<ZVf9GB;=fNmO`Qyl+!ETWGfWrT16}A=2-u1ZQ0N6'
        'w{JPTKE-+pcUt!EA0gf0wZ^q%H7!ZCG6~nX^YHp~YlE#V{8kcz+rs<uEuk%I4_l@7Qa6#pvm2z2u^hANn9cINM1z6hB;26QPSs`rHsTH1'
        '45~JLu+jD9-jK~XZd0t0lwYpV{`okId$~n$*M_d2cG;Hc_t=(@$BjPFJ69Hn(o!;~WK!U=@RR>gDu7uSOX(+Gn>`JEshnq#fSj!_O3jHa'
        'Yg_jJdF|nyRG4g~+yb-nWfI<Rn6WW6iS=t8!Ea21u0izruJ-w^6qt{&(3T&Md2V~SPkP0+RKIGdxBL~`V*M&}Z~jZxrTP^^z3DGm7pJT+'
        '_a>_ODCG6X2dnx<=!>h&ZQ@e@WVAN;O{(inZ4(zOxl_nRwdvG*&md=JEF_icEIQVP6k2Ba(?3W*wuoc7WE<&|`>Bqq8!{<1Wer&=CKOhb'
        ')*$re2cpIl(-&vSqzQYnXhNR_5+hPGbOcK4jmh;h-cLlIq%?I278bWV$g11i_g4GIw8pt|dw5VXiMA(!vpOj_dEf=Ne%rdMU-$8=ThFc^'
        'ulqL5Z@*sK4-Fk!w{e{)l4rz2dXZg4J=t5@J7@G_WD+f;#r5-%2I4ISMo=jElb+|riA{)!04R5Klt>0{vFL^^YE_H=?_5UTcP`_`+Y$Sn'
        '+Y$Sn+Y$Sn+Yx)scIar<HLYe{vn?z#*R$%sidEuut!}(cv%YiRd|P{F{U2-3^<oE}bXt`MT$WR`Lb&X8RmMDpk?^l}CY7R8Dfc3+(t7db'
        'G+v~xab6za(j7i8L>ZJ?|18n~UkehEE4)IQgj_M@b7_!IDj}Cb+2eGRk;}e9nu1&|m5%c;5xFVUQ6GWSNWnyA9=Vi!wT>2^>q84dxI}=2'
        'w?5@gH6H@Y7P8i#K+5lgVV&Zf$?d9K2gjG+TSB+iox6(7a-aN4o7uFWZsaO9y*~MsHn*uGYrM@$pZrRja0;t;U&SWcC%@8W2F<8Hc@>)('
        'G)tB15aIFn=K1dVUD4LDdx2Nno8h|`GVWdsdWg_3acqlF$31&laEHk!jFZQ!`teuj$8&kY75Z2A6Qk;nFE{0y`YBh{FQo!5PZ}pLq!KPq'
        '94DVdmG$e<dYyOHhfW)tiJ_Z#gvRpIeEXF060mj?IdP)RX7_Prab&Zho|@_(L+a1%5_xHAv@HkJv9Hu%A^m`RvW%-)6#A6OqsPhpxkTV`'
        '(aLiu#meNx<K$VC%jG5G<n1&Cqd-8!E|<H=$>n9^<TqhuX7ciJ@~JeJ%PYpo6DW?Cn|Fo&Y_6Ywg?!#s<@J=${Zx*VS7CKv`c>oP(`hD`'
        'SC5nDk(K)?7$<L_GOk~EMSc3Yyl9-<iukODJZzkN3QemoggpF;`Y7^<aq>p^Ve-gv@*bK~&*~X9PVS_x`u&hgSI8&V+aXULC!dLxV+G_X'
        '<K(kZpBEsXI8HuIm8XuAN0Y?kmNrhFq{`E;kY{pv#%1zAJa5fVXOa$m!SWCCJlk^(d>^o=Ye^_}5*^pq(Pp-#knqtOXb2yt19t*10!K(V'
        'nF2JCa0K3t)B($Y?Evom!3b0Wi-0G9_enTv2B3EodPkvm^cZ&4U=jpT)ai7hC<uIa6Fz#9U5}^mYc;=i@#`Xfy_a7H`1N^yJ)o0t{e)kK'
        'gK#~znO~oiWfp{>OcFi}izp!Bk9K~2Fv*JRY<^wAug_%R`r%{6xE?#eub(Kk(Z#s?D!(3KI>$Wh`azU(m&NJBXZiI_T_Rd@m|sux>j=9Z'
        '3**;Jeyy{lL30u7AS@WnPcgr?Dc&MVP|6qV`jMVr<FV%mAN29--D-)5^@+vo`cWzClaJc@bw2t|`0xZqx$xmRtct>k7x;C=h&cQ)f?ZD>'
        '#8ba;B9LEGV{rYjEtc_9{JM@^|Cq~pEx-1#>jwk;`aHi5^6Tg9dfceihxpk;dHIo_C4pY(IDvacURUMiz$i1x8}3>QxtYwCc-nc>Nma-D'
        'YQ!CbykS)^v2&kJ-f*7AX*46wI{CgU<QC|gX=g`2=Byq%(^oFStPvo)8?qK|_2Vn7LLnDv?RgWo5^45I6SZ>PyL`G)s%{w9{SkB{k%}}A'
        'x@}zd4zA1E6ir)hJ*C=BQ0>CG-AdT8)kma$*rmftrbw{K+`~%lfkiHg?!W7EocRcB-O97vSCp^55!`Mme5G-(6X2npd$^5zm`^66At-XY'
        'P`*pC-9MFUCquh~Yv0PX7ihG9#p*nAX_X25@39u>Jq0Vyjq@^*dr5~EC-<_Dd+EE(i-~!;z`Q((R_Hw?J}=5T#!eH8m)o$*F)s_ams>Pm'
        'wsFe})lwNjY^CpHt(P}+tcQFZ>S!GO)5?on-=FE9JEx9Dug!hX5k^n1B3>J;VV;unk=$pX+M{fxWT%d*m8&7&!rDFh7%%s6HXb}V=S@ES'
        'FuvO-^4iz0Wb&=7o}*j1{x8_b9esIKFy}0O&1(I<wi6LUW@}e%QRmSoxXya?_QM`q$49&lX4p<%aSpwJ5$#F!^g@~iOA}cZpTtuEJK$G3'
        '?ooX+&xyFN`sVe856jNc$5;L{_y)E<dCGZ=H{jg!h-Z>BlI!@P92d%Y5-l}*BwkJ?FDC@$<nZ}a+0?HoXClr8YWsOPzaXRKm!ku>4uC(v'
        '=BuZem-7JWEt@<=)1uKc82zJftR$|@OFZW*@orXP2JB1}%uCeC9wOV=*iArvO1wREel;I2W_f+bBT9@dJ4SzY(|+)uvKjBm=VjlAJ~4Tc'
        '@0L(X7@0=jURA(b%cKo=TfI@`W#go%DN$)-Cfdl#(aZCB-)!Pz;#tlgWT$mcHZSLXcE<2Xt1{s+l$CRLIc-Nh45R<Z$G}#l#8oZ4_vo^6'
        'ykp79%jxI7ALZj=59dE&QS&75at7c>@K{&5e6uK;&qZ|>oxhFGXuZ5x^}<Hv3!FcVUf3~u<Y+V>%>iWe)U2B3^P-$o)ERwVSw-~nGH!V%'
        'n>QZ+m5-wJ0hWQ$H|Af02i}HyJ?^hXT@9W{tkT;tqJBAgaxl{8H=RGDs_V^pWqs4jE4be~5Feu_VC7RN0i)PcYyX%YK?z$&e?Rq2dP3<9'
        'UcytD5j#guy~k!m5gU`T8zZEckNp^R?0Z+mLhq`$(zhy}S5{`dyqcF=%=%EiZRKiO%|@Fi9KL?U$3bPsa~K=p=)cap-bPRAeKjcLrG5#`'
        '5<U}R)R{1Wmq<8)tLyqo-@1O@x2~U8b`ib2hL`_C+&6h5;A;&Z`9(Lyqtpmq>LH9%gRj&+wu*Ru$45{BtE+s5*FZZnyzS2M?no<rI}*xm'
        ';pMLNm8<vMgc)}iB4GB!uiS%i8HI9Z!YgK>MHpSM;9q%!#LG&^;U$!N_s5mK{ShTJ^Agta5?DO-o|(J^C3aSf*cg;B110deHoB%`@7Ufp'
        '>f77SpXGh4of#9<^S^I5f6up@pZD$NkTvldtoMy6y=N+Futps}R*cm|)L=Sl@B|w%qxU%KmHin$w*1E_&3>djdl=CJKf`Etu-3>A`sRS%'
        'GX;Ksq>g;+xU+*DZ8FI}TloOrb;j-l^3R2S!259g%d4fHfBtw<vtwz_PVcd^lW_7VaE63Gg#o2NJ8&=X9B`V1PjDXn1SjiH_5c@1_&1!$'
        '{;d~S2jD5<-`pgeg3T%LKjYN?=Pe|h#yS3UEdW2KkCX84Ex=w9KE(<2Q=G;>iv#WlAo~pEe}?iu8z$j%)Is1++jwYZFT`YI$bEAE+QlbV'
        'oL=!CE6%N?m8O-!D<fA*cV*mFa971$*1KBn>bz^lUA^*Q`Ca*d{CoMJ{GL20A3}cIo5ZS@fMF7=dx8BV*0cdzMCJKj#EY$#gMw2S6rJLr'
        '&Z!&JJN1JGr(w|OG!B}ara`mQJm}~28}xVj4+b~`1_PaegF()q!C+_bV2Cqh(BiZVhB`wB!<=D*;m+{E2xr7#q%(3b${95n?Tj9bamEbB'
        'I%5apoN<E_oD&A)o$-SS&V<24XX0RzGnG=yQx~UhPd%0voMunEGi^`W>Gb&YuJrZkd(+QlWM}kdY|c2CDP~q?F3Ef*^LUmet0`+u*7I4P'
        'W+!I%WIvF-Kl?&XZqB@%Ejfd^`rPW=Ww|?YPvnK=wdCEM_d?$1`BMJ0{0H-2&mSttFPL92z{iE4ekOvG4zc~#%;pi`4+H>#KoG!2T8PYF'
        'WKq*lH4RhKa5ark(?~UqQqyQPjZxEBHH}l#3G!txiXWFx7?)4vpRNd;t5=qeNzki$qtWc|9~c;Hv4llLL`B8K#l<HkN>WN{YI<g7c6M%l'
        'eqnKOX=%A3KrjXA{6cOdy*z4|Y3$RVV5}%%SaGBHXIv8Yr%dcpg;YwFR7+OO<rbVSXHc&NnR-wCQ2m+ull6o3FV#O;zqWo(ePw+}-Jj}q'
        '*WFoXt<z0<ZPJ=aCAFt(H`m%~KCAgzO@8%(>YJ-BR{gXpw({x9?26|q^2>LXr<QFk3oQLn$-kGlivOuNx@cA5`-M3LKh8grpPF}D?yI@F'
        'oR;hdvfj-yW?C~=r~hNx$+Xba`iVbCc`W%==}(d&DLv7VaC7`U6Sl<t_t>{%PDFnhEkuPyCP(Cl*Mv2Nc3NhJd_TB9Xi4Dh0jvGj_}ydv'
        'vFY!O_ZjZj|3vo_alNpf*30WFZmrr=^U<UsE7^t`KXL4BeyFu;a$x(b&Uu|QWpnq$o?X+jr$0S2YSz8q`~0T%IXl_Uq$;0jl)BjBzh`Qb'
        'i+z~)YI9pV`{??e?#Z2O^E=Yn*n>3xWM>c4%l)Fw-oiev9<tk*JWJo!)XhE?x7cmCFXj&bPqGitP3Q;<f?tRjcV6)(;j3dLoPCFcb7m6G'
        '_ml9=rzBi_aFl&CpqKqF8ZYXHg%JaP1|;mSgtG<V^us<aW1sx6&#DRgKKoRStrqNgfrwSHK;5&={F6K7*&tZ`j4+0;y6jVS_NjyNOu(K}'
        'V)^H!%5zU5e@-C&DUQTXP0IO+eM*z2o_R9(C-#JKMSS<n;^!dZpS<O>pK$`iWc7Q7to{;N!$n{o@H$x|QXqQ*I7`-m7T`(X99aX~fTw^9'
        'WDRNuwgW?C4R!+X6AXVLoxl!Ygshe>U?<=qYiJL!i>zplHEbI2EXuhTc$2J=MgZkRJ^&mfYg8~<V;%-x0#1@O)(oJNVrKwr0r-qP44~{d'
        '$m99})GO{7Stnq^On|Qm(3=4J32w3qCxFiZ=!+;zi~!I^5w>C-fVPOU0QeDC0qcNG0DOwi0j~lF0QeVA1BitV@zS9zT_TVRR0AkihqmeF'
        '1IvN6Bxdyi%YnNAJd<Q?25?HvdJcF6cnf$R_z3vC)|Qu<7+Y)0oERHXTRSl^BBHh;F&5Wi&<GMlot|wy!dDLgj{yU~Q@{@L{!plec4AN$'
        '`r}j<g|QWgF%ysR6@xJ{5o0R}V<Z)0ClzBP9Xt(VBL`z94P!E+!bsVbK#h?KAj>NUDluHEh)R$zLB1p(Gq3`)GSftrxj><bYJuEI0u=)F'
        ';5D$Rh0P?Zf$D4qDucYtCQ!Lupvq>E>KG=AWNXory;)BU#X4#z2dZ@B09tBvG<lMa+G~M29koK%ihL9FTcF>HbTZO*r0otpu?0LB^Cb+3'
        '0^)#R%%|{Ffuex8WPuWa6owR$Qd31rDF8}DDnPy%s3_1;L4}S=fC`|t7>{@rdYS}S8A|n3RA-_xpbDq~taTVnR-na5^=(G7H5<_mBQ;I}'
        '902lmpb4_3DkHTv8mSF7?XYQrycKXZAm8jyPKQ5DLE4RUD$=PP{xrP}==3N1WHZ^FW@=^VG?R0(A2~bx$Th`}rU0&<0Ga|!1)OsOs1uk5'
        '%w$*;KuhKZ(yccGeSx%OQ6SwmPX+R~A<yDx#CS1be3&D#{9wI_z#0^3QpQj;)~P70FL4+%67HqpnWG>>prSN^a#)JErWWI|txzP1rMZY}'
        '4&qq^pkIsffl?inf>$8TMr^YY)9e~O<rV_vK$V^fkS|7B1D#sv)>i7M4!jO&8SYi!UNr-FO}zo*#eldPFkTEwU)T`;dc+-RW2=$sI!x4r'
        ')HcP07@Ej|w5c7BLP%SC@JQwWkaq$dPM{lns);&V&D7Ikrs;_L45YJ=I-AYZX*JW-E<c)%n9l@eIsItb9DkZ|lRwSu@uykS{AmvIbEo^$'
        'e54DIUohLB$2S7;i^Lp^!aRz`{EG&c5bqSkI~B1{1y4t8C9LUEMk1wPjZaBSq=^j3XQd@kRz?y{fb9hMnvjXd%DiMs!1|w%E>c2HG9@CP'
        'h%_}Xg;H};s5nick_;dVC<IC*Dub*n6UYW2D}yXIXCme00R=$$MD!hy4-^8(=T_?|4|6ad{ZNScSjzeX`N9Sr6(e74MZPhDDw-mw2Cx<B'
        'sS)#~3H|Co-#8pVvw@nyo58ElN7a}U)tD1Cm@9SF0MdHot(X(m$>_5NWj@)!TVOjGcI`;pTMd{i08(ow?mIEZkUEgIAZ<lDnE~8|v=eDJ'
        '#y~fC59VkO^3#x?4n7^S>0L&eiTunCU<&3U#zp%KU^d1AxD(v@Jrhlz2HjcEnF=7C-iz@uA6RInncy?QXZD+EHqzNhZ(0E0UN7>!;2m=?'
        'uHdH=>8#lpJ2&~!O-OG-I-jA}4`T)RKKOi$pFW@;_yMpK<7feZ`~u_`Eb*sXmITnkTY;qk)Q`EnaDE^yM!FPO4!-=BK)N0L_FI(s9p#V5'
        '=nx>(NC`mzc!C9p0Ahf6Kr&JW(u^b{<si*vX{~|sGa@KI8_0{G9Hcq<Krv7rK?S&9QWQZYWf8u#2KQ@lzoZWLu>O~$zskTXz^lP)z-z$k'
        'z#AG3WCtAZ<-lBaz@Gzi+tFaa8j5j*HQs^Lf%U#|GSXJ;0Sw@6NZXJ$c46NDTAVt}VI8@E9$*^Kg1x2<m;!VIv)X}9pj)KbE&%DmMMMji'
        '0Jj4xh<*Spz7>G%R@}Q4{8q*xUyl6k42uECmcwQ)@_o0#7RS|Pw-Bvd1*`$?0sfBYj=PEO#8dm7Yl(h@dw1PQbT`s_AiD>$dsh+fqks?`'
        '`*1)Cz?u+%^&rRsgaTL}g0MDNa2~W+*#Gw9pIDHE$>OljK^~6tVHDCR*hMp2@B{|vCcs}T^b&yh1cBmH1WH1hgfuw`kg$F;AfJYO8rJg+'
        '2IO;-b(EWexepY8$ES%DpDj`n(j=tG835LzROC~^)36q$A)kpf3$je`EFga(d}L$IV8{iqClo_goDP6jWC4&@fL8)Fz$73Q>quI@p3*Dz'
        'lu-s?-^fBf3-UbhylP;Qo(d}xsjv#D1@hRK1j;b(iUFh*$X6h(LcR)VO$qYQFP{Y16R8Tk3cL!uya{_eP>ub;$^c#in@O;lgtQ($Yz*Ly'
        'W!P)5el=oGx0eFob}QTe&DevmZ#S8-=7DzrQ;=sea65Q2?oGzM$w*yDT}V6Fe(v(4o4ft#X6zsHr}|SL(rMVMr}YP7y$Ph5nA3gz0n~@R'
        'y^pN{{mNRv#?WrreaOAk9kN^AeQ<Z*Zsaj0Ls%YTjdru2XOLVdFC+ST?zwv|>{dSJ8OGBH(~wW%q_}><ZxhedopH%`pV=tymiNgY%U{Cv'
        'aG__JeDc@t{rbHEyUV`RzR|whe#kD{<vw|<d{CCzonOCqBldG)eUE(`xBu8KuU9N@+&=n;G3(=YP55nrTrba+?~%7b$6Nm2`et!BwBL$|'
        'ou4b0uz0ZiTtsg7eIp?(?|N(ZQ?h($NIraMJ<}&TUo^a2e%fpQpYys_6s+6U?so6{^?Me(WxeH~1zl;CJLP5aTKQplkL;FDe%yIz*`e#('
        'KOOsZ?AKXeXP>J%-+tkyZ<c(s^wPGG-J|=i*9(e0i+}vVB)LFdDsQ|>?5|(`+0?Jg&NW|H_|1=oemZjSKcg4i4)+7@LtguBG<sNitMje3'
        'Z>f1%sa2Bv%7Niu60w@GdY${>g<ZpgM3>jc+Y1wZm2|xR_}t_79DnlN-Nz3d|MK{kU&vp`{}HpEwH~MBmC;*bU#WVf^}{<3Z1`~NhcAD4'
        '{KLO|c<ICVgKT78&z{vR>Ar&d>hE*ixAeYy?%Q_X?)whicWCFK<Lu54|8hP1>-EC_mATM8{NKJDx>hel|E1zDKm5ziOD&hyUy4~zbk4ob'
        'J>PA2d-c8d!|#iH2_xuN6Mn_Ct~HjvNxoDpH$L<wkA|mUwEhd%U+y`+?fC8khkpO@LHXdt-qE%4p1(Z`*D7D}|NmYuoRfw+hnIQwc+TwI'
        '!}3vmak~<~ob%;FU$VMiITw=1g}q@L9U`peTZz6&zF0icAg?X3&$W-OXxEIwvXQ6c!!GFF!}kQfBdnLVvbuZvznOT^I?^dG|KHdPy!@;8'
        '!hdG@f9t-mjU>#2(ZikBSdqrh1<#p_nZxau9`c;_?74m~4ACX*>_eRwZysJTvUc=g&ki>7l^ufZ4{UGvwkTfR9t^RSe<S|_@88IWuV+8L'
        '7rv`KtF5=L?0u#EckKnY@nY(wE_vCF?h7~63w!hoBA^G1fEn-y0)b$_0)zn(Kok%I!~yX@A|L@NKq`<9WCGbhE|3ot0>wZnP!3cA)j%y!'
        '2Uvjyzz#G4EkGO44mg2MpbO{$rU5g6S-@;y4$uqC1LgyLKtHeuSPU!ymI2Fw6~HQBHE<`e2Dlqo3)~B=1MUab0}lWX0uKQX1Dk-yfX%=b'
        'U;uamcoKLD*bY1c>;QHGyMSkb=YT!H^S}$hi@;03E5NJ3USL1)I`Ag&7Vr*m05}K?0*8V3fg`|C;23ZmI01YFoCH1rP64NZPl3;YFMu<E'
        '8#oJ`11<nVz%VcZcxaD^{uj~zBKluM|BL8<5&bWs|3&n_i2fDPzasiqME{EDUlIK)qJKs7uZaE=(SIWPPelKT=sywtC!+sE^q+|S5z#*)'
        '`bR|ni0B^?{Uf4(MD&k{_=||Yi1>?$zlivYh`)&Vi-^C7_=<?Hi1><#uZZ}Hh_8tFiiod>_=$+0i1>+!pNRO0h@Xh~iHM(w_=t#)i1>(z'
        'kBIn)h>wW)h=`97Wtt#2ox0M`U(>hOYU?$}-?hfYHTvtXns0h_UI(i4IYOPs@v5Ip)xJ=*!xO6aRH-Mn#<3MnPi(zotDT;&eM}?3XbtHN'
        'SsSt^<OSD@OJCmh+kJb#{1|IsfN?4I;f=J9_F)g*w{_od_nl#KvmOszuKBKYu5+&6@4K{*NnF^omqJ&jL%)@OE6c`E?XGXcQ@?9y<Io{~'
        '5(|qSTSGm+yB3Gmb#>&!(7LXU{2Q)y9Ub`>ti<c-Fs<wB$iE3HKbv!Y?l<>b+%|FuXM&{{(=T=7q3CJ(cR28fe(k_CpchyKtN<Pao&jD2'
        '-T{uPdJLyX^!En}0oeJk0k$jm{NHhhoGm!#y0CO;<M3`)8uqzE!@GwzURZk0b+!P{+O#hPPquwuuK#iudosrB;Fr6;T>o+3A^X0R-SoHG'
        'EBl{vRrWsh>fY_%eSU7p?vUMc=VBBd!p`rO&&zW+?%ue2-`t_yL%T1{a1XgJou3gh6mn^9r`zp5f4+00d*k_ibIrTWyT8{t<Q_VIiT^}g'
        'j6<B_9s&k5VEI+Ry&A02=<fiY2lfI70K_!z6X494&IBhg3+Mxu19t-t0Gl;Bi1mc$fLDOGfcJrqfX_8$4I^&^!hl2|8z=`FG=459h!-^o'
        '{tk%KAYDbViaHJ2t_5#hl+-7mrolX532-NHKd=dSQf=d+tMtS8K3FyW{#E+p`K!c#)>Y#D+*RUhyiVQcY1**l2HJu;#qUz_q6V*z1D7jh'
        'N5fY%d_}`oH0+{rFB&@0gPQxtfz!ZQz(ZmT=5I`t<~|U!Lj__IgE1O|F&cw08iO$!a|S@0W6|bVjH%c}hH>^OD)ND9pb6*#<^X704EiDl'
        'eG-HIh#6oQ`yVLr@u|+#C8<A2eLS}%x4(34=~K1V+FraLEiEsdS$aq5gSmyd-MP230xw<~l`rAVPn({RVF0UGIBF2SNvTmdA`y;Agd-B+'
        'h(!1xa9q8ID1@I?xko{SzltazMMb`f>T94$gRZM!j*3MJ!tWdpr4A8@N(7=3fv7|vDiP>`2=qY2UUvUqzc8DJKD9_BZhBC|pJH5+IDbLI'
        '-_htC)9_D8w7|aw{w?rtLGN0g{|?xp+A<&avQ(+NlAb(lHWNuG=h0LRZ_)6@8vZbeq3I;NY(`p)G*wNlNb8W!R@2o;+mJq{rhAk!6|U+$'
        'rqL}{ZN0wIRo}cUX8Y9<QB;1z=JD-c9o6s;H2lLc4qJ?y(Bmpjt2oP`*5#~5?$u*9eCB=nj`E-I;A@wL@6qrVHT+c)!?0S0VHF9(>Jc`N'
        '`Ec*`)#r5$KcM07YdCg+F!WCt`Y8<k6}Cf7mHuMC&d&Zzptxyhj6bT^@IVcZfgRcshBk$vePO)KBg2ATDeJLZ4KLR4N)1PQ!m(O}W3>p!'
        '4isMgEyz~&!j!st<MMEX+oM<gJv^Y{&uMsyhL>x2n}*NP@Z}nQzlOh};e#6fiH3i!;b%2`c#J<HYIvB2YwGz(v4%Hjc)NyA({N4wA6cT|'
        't2KPRhHujF?e1Y8-=*QtYxqkVzF)%+YWR_}8h%crb3wz0G=7FP<!boz8vfGo)%6c*_>q$yqgmxAHGF7{Z?b53riRyQc)Nzr*YMR^|5$qu'
        'XmC`6PgI;yfgLf@sDSaS$s;lTBQdTcF}@=)&LjI&;B*&xw~FOAAjhdB5~q{MhB0XOlgN)WcwYmY9wRpc+y4rVQ%~e88nFCZDn3_nJs;kB'
        'sCBz?AN)43wczXXI#HI{6UV>`g$FSHsltQ&z>j?Ol@4oQ2=<hL{b#^M3TFJ-FTnMR&ZfVB8|47T?=~nt1HeC1xL*+XA57ejIRyMQf5t^K'
        'PPzmC7zi%wxz3|Oj2r!!?LDjx9@Qt~qjDhQ>(7CYsO6pqzeGW-KHD#V536<i2K=H@=NB)652^JZzQ{P&IdBR58?}uJ=l(xZxDp3me~(fh'
        '&PV+M!8hm`A5rS81E))7#-A1$A6D8WFn*D@_n&3Phm^K^E^^Lvem@E>f1|`l<;?%b3Rhx?HDB<s^+tUBUK$aEVU-VodxllH7@N)d^AdDr'
        '<^Bf7N#PrZE(%cQ{O3#;z&;p@({t<+W;e>pkoo=foD=5{5$rqy=Z~p&*#Ba&@5S!md)6gE7*+kssLzO69^;qPdZ=~`Z>hkTjr~YPxz~?p'
        'R4L~oYuAWUKIfN|dX4#s3s!-rqquYhvHa<4Xjl|RMsOYyA7@V_(|AlgqNvEW)@t|(4S#TWj32o;#_bCC<NN~WfsD7G^<WH+ural@=j&m{'
        'EsW3qdRP=k75NIrNzq@!xK8o2j+J{!!-rHI#?Py^m*D@L>T_6RTqz4G;7T0?&Xu~UZSiAmQ8~uZ*2gaKda`l1^@&Too@NX9v!kfb$S9Y;'
        'Dl<-8|4or`9rtsP$K;a2zhQhx(K*lfdByf?=Kq}H^J|fFUe?#B8#u3n%KgU0`P9WxFCSP8xn7l9u)uqDzPKp(__=R%Ixio&;OFH7f#*$p'
        'U12=x{HRW^@U8I|#(3^|k55OsFvhco#(3$FNAz<0@EC6&tMfpQhR@dU`5xIfN7>UmKci}7OD>J^ehrUMxRLR=;Zep-j7thPGoH@4tjKd2'
        'Cq=)Qadl2tvN8dLE&=;u0`|oO?28GQD+$;a6B@?Vr;_mz)eqyBRR4?*tK}+OsShZ)Qcr~|^;gPGz+RewJv9M)Qv&v;1nf--*v}GP8CPzd'
        '%2CJ1?H&*3Ch#^7Z?B%s+fL49E}tfk^0kQR&*ofNi{`22VvkI?Kw={Hl0?Kk5$i!B)`G-hKJUlMoyYlz;%8XqYYO+z`LI&%FmcZ7Gc0h<'
        '>#1^Hf2G_+l%Kc+z&@IYXXC_80G^8zcX``8JRFFz|M-ewS@d$Ii}QZsY32FGoAQ1;qx78*EdPnxJ{9qw@b!(n&m#}VjriI(aF%g(EDjCH'
        '-ZgN*GsYiBTawgIsFHBjNy3>YsZY)Gbm$!7z<%*qeDsB(Gd})~M*hyw*)a|~v^xpyPTHm7MJ-g{FOGv(m)ULdmCM`o=m|wf^5@TZkh>MR'
        '`_nUC{+NDD4t{ow!!8BL2dY&xX`$5Ta+yY#*`;{PRiCk9R2}|l%CVDXuiSp@q@S1fA3Nsb4{P{KC&u{6kB<4P{O9JAKKai}kDY+r#QAK_'
        '*>7Non=?5N;QZW4#@TOVh`-p+Z9};J`cIFT&HOhs#GmhRA2yl!Z)S);KQeR>=j{y6&(S*uGykm*akGWnhH~D+ZDToK!)=o}e~Q~?alW71'
        '7IA)p+lKQxNZfV;=PlfJBIk>_Z4T!Ta@!Klcb+@}+eps8`1F{!&RZhfM@(uRwp0!sHmW|i%%Fp?P2hY5iOK4xfYS3C>{W3<#ZeWXsKES@'
        'h84SHjQeD)hsjtEle4cP!`w>78kvkWEE#K7GS-u1od1)to+aa4o`SU{8S^C>Yg;nTE-6@Nk}-FZvCbvq9Fu}ICmGKW$yoD}KjNP&4C&Q<'
        'XUl^c{+Nb8!MKO~8Gly8UuArR*#5rdFyqR3cFQTohlrgAwhWE&tw0Lal$6uJS-_)UBF?T8qX5i@iTUGDt?D0FKezCHM)J9apVjc;G5&<8'
        ';r<#Prr~iKo}%H|8eXj7)f(QQ;cXhuetk+^_nw%g;qx?nk#5MxZ`bf2Y53o1_(lzXT*IH%@Ly~AKWq3t4S$=&)GlC-GPY6|0qRdCQturH'
        'oSjn#u915A)$%)3Jg;IegIY$H=AI%m_=ki=#l*!YBqk;$Nm5c$Vq!vkTufAGkQon1sB;?D?KJpJ!}w0a7*4|&O8dn7Te=3!igbS+8Iq7T'
        '=*Wmslzu1sZ5Q=xrm!leZx)nvm*|s^PcgT}^u2l|J+3FCg!GI-Nrw$Sy^=hEiqzlSD9qDSah`#Sv5pk7-;60VQeL5n3bRDY&+?<3EPpD?'
        'SN_*>UWq`tMIz-uFRxHfdBq0G!N@8ogr5Q<e3>XKU!<&DKg!DXr~ER-ZW8QjN=2$F(@}Mao@z@BRIA?0O@_}jk+RctNEMlc`laTHl#2SL'
        '<|(qwLV+?+c4o1TGK%$-S!AG$Lgijol|b2*D7R8a*;R_Hx>TU5Vuf3a1gcYYNz6dBGjL|fz}X@LdrbzO%QEKpVu(J-SRznJCeqhUSHw7Q'
        'B8i#)`YYs?LddKAeEH0Y>{pdjCz|n!P0|_k#;8QSXfT8)8AP2iN)mODOf=~A`q+s^yy7RC@QO=e*I0gyNn!G6epP<1P~{LrovyltO@tbU'
        'js$%*)=j;>)~dsazupRG`uau#UJXsms-cPbX=r41Xt4U>rTA$qgCApknMg)`T?zac>x!_M8|%spB<SkP2zPBo?03)<eOI?23c{2r?Ay&;'
        'Q@p=hE6chGBo?$N?bXGDcd4AoXKUn(HS#qY`9m7{QyTg68u^<V`BC+MMlt!PDrfQ`h5O^wR}iCe>>@?;6&`{op|YK0eya3rS<MO)6Tsut'
        'UyH}eUbKeGv)PkV(H73j%>m#qXmk#9xs&UB!TBtvQ*2gsuoD!gYPeOyXKVOs4S!6-_b5CFyd+n{%hg}gXI%9e!nji%e~eG#^|`?H)v}D('
        'J&O-AdA4Z8lVUNib9S&1Pmm?*_am8D-161&jriodRgU-+=v6L|SjpR--4=-0R-RPk0a%j?Rh<B=JB4ce1F%aMs`d9{I%=K$n9eMfGo2M`'
        'e=+~wa`8k^_!KYeMYb~+zQFnG>{FY<cQ_vmLR$(y<@~rQ6nu#D)6wDR7v;OwNRJ)4NbTQ1)S*aHV}kk@ddD8DirQ6<Iuxm6SYq-SDrfRJ'
        's*VnE>tki*bm3`NEK&P0n#mvF@<q{1{)Acw^hHso#`Za`^ANY)ugIgA{CzIp5yj-E)Ul0vmR#WSy)n=!<>NNzKn!$B!#F=0V*r;p|0D*d'
        'tWvcf^@vqzEte1LF$PN87|%885x3GAoQLT#21@(XF~Ia!aCx?g>EFwFxrymN%z1-}=|8D{9Telwa`~(X#J}_v&if*8$}8o4l)F3veNlRp'
        '^SdMP<XWn>i+!?EdX~#K^SMc!Z)fAVESU4>LNKSwly8-!IMksmo6FydL%C&@oWCE3a?6@H|0oXSmi4InDf2Ur%P$z1pJj~ai3W`Gvb#AC'
        'HlV#_4{{!7KzqvueBbFPmc7X3bvSW|Wk)z~BgDDvB<Isa#JP;uKX0Ch`j_+i=PhA<UGC5Mog&IAkKz1&))(dJoNp4*mhw8zpJeeb@8o=!'
        'i29fF_~*T-N8HNSasIj(3C`z2-XI&#<<E0|T*P=TA5{AqbE;ga+i45tRQUyUoI|HlX}iY=ol4%1`Tj=eRPGsDA1V&3Uo!??8K{w;(#X}h'
        '6NYwGysGLjzH>~!vPGk>S$`^qH1bk4&SC|kRe{x}0%zn3D}ZxV1<ptn^MO^sdaTaag{x|TP5|<%)t&9OnT{^0Nb2sfb)mo2e6cs*(`B37'
        'U~9*Wo_ocM$SM_8RhCLN=(bADwhl)(QF&QmvE+31NL`NZPM6c(>TJfdS+}dHXS%J+AvtC=Iy!q=T~5i-)#d8a_@$;UheKi&7+)3@NsX>n'
        'r__m>UF|+<Qp`2F$5gJOQ*r}3rPtG;#x94wwWqsULJxLM?QTKr)SxLL`;Iaf3-)C<{;E5TE@yL>t!HYxjm5Sb5ufRrDz(_AInbbv&UQzS'
        '!;ao;aY$3!ds;heJr1eM*64s<gUdO!TWW&OrdDTbcMJMUVz1)D+`>Yf@@`*nv()Zr>XBAUMND9Cb=cW`cyV+?n8&0{fRkm9tH;((@Pli2'
        'qpQn-XQNJxgKnjoO4SuwEcLh~n|+$i+2~MWtXN#e9nKy!k=44(=A7Iuxtdsp{a=A=y_9>bZp^>ZqRa1no87nFbIib8P&~6>-MG(A1iGiS'
        'Q3-hU<@XvK&8<!++BoiBD{FWM2Aa2{s->LE%d<JUT5auDzbAFKwzto`+P%v<|4O@SHAHeub9Bw@;bVi1tI1B+bg8vln%?DNqvg8x-Vk84'
        'sSPI5Hjzka4%P`)bMjV`XgU^(c3XGPWicJAJO<~D>5PqXuM+P`Hn4qRzY(2raZv{tD~#X6B84b*do{WJ)$A_MVPSM4wAH?QjqR;XP2I`Y'
        'vy*JoT&;G^J-Y*oLicsclQb<wuFKJ3Yjw8zdh!Nq{nwWM*S6Mfb2dA=s>kki*t#0p9lqRDXJd<_>k7M`F4t73b=W35y1L2k=x%Ms_$$C_'
        '+1b+Ch?Ty}1>VsC*94D~rA9}W6YD<P&Mv!&@zEePwcDJwW*eRoY)&>rUAWP0>tKs)caN)aGTZ5<I_;8DPBpb)jyBqyE@!UP&}FkrO;g+3'
        '+30Vvb=moT=vAa{seNXy)W%xX>}qd?fnDmf*``isq}w^u4zuo=-93&Dse5W?r>m={ni?g$Yr50nwC744Qj4ppi8<(M?df5<-W}Jc-67f9'
        '8*EdXTbOo-4OPgMx}**lTWNju@oBfA-rYTzj;>q@)tuVZ<Umw9rH-2$v3|Fpu1=|=)!D*ln`>$hU!3{KQPvY4Ki4$YBUf2rxP)&j>ORIL'
        'U9HV6mrK}8cG&DQFJEoD9X;dL-)2{`PtpP}QkRPby8Fs<yz9edMRA#Srz-a9;V|Gpo}WLiBJ2K7u)UGJ=z423?q9yqV)tuyNmv<e?JlR|'
        '2JAar{8T2n8rmG}YoS~X-HlVb*s9Ru=;kMkcB~hjHXJ3ee<-rf<qK}VT^fIe>|45AaxPfCLh4y?`-*RE3o5CR;SAH$>YS=<PFlU`tvxMQ'
        'jRQNINc<!%h<fU!8Pv)C`~d~&qm*ZlvA4*-OAL>vf2b`-_E>GUQJ}YK>+v8m*i<iwWP7g~w{8DYY|_z7^)8)E^{=$T>q~Z*tf%JvjRu*N'
        'FGTw)HVhvAJvN?w0scp6u>CNtT+~t8@k(4P4^`q}^vw#Z-av2ESPe#+^t&l0BiUbc8O`)hc9+qg+FzVv_M_*!yZi#^+3p$s0rZ3IKL`k<'
        'O^a^}^p<i|%3)u;|A$;u{Qb;gK}Q+?QiA&YuCiWdpz3#O;s3Qst?*y}1}o()Ydx-SwoNvg=!G_?*+e_rzlTzGc7M;$pQb)D7uTof&++&A'
        '|5NHjN~F_iiSU0qJ5fv~>1cj(3Z<XOOiZGzqxmTlso+Frl0<_QWs;7LR1{9sQOW*hT?D<}Sg((uS1VdjhuVV`Mm<%%(V#QZKXthbCfYT3'
        '7Fs&%zs)iE(anF~XZEMRzqt>k-}2}(v~+9VqQC&!c*_c3OaCJ@$|zInA99Tm^kG#~gn=4fZ8GTT<(A24@5}9z^*Z|H)SK|I^^a4hqX%d1'
        'Scu5{a#laC{W}&4M890vFOcdj@lO)#mk;o$LpdiWB5oh#X2Z|h`PGP9-tTJliPZ33Ib!vDdv&6T_Szk&ZNsb0CNsU@>_o)cpYOtT-tMWW'
        '^VVAyq0T$|XQ9qBwy>H{-#N=ya}qQ0l$E&%xEH|FR_61-8~{&TnGXRwfV~t>5fn*L6s^9akc&{i+urw_%*UtlzLtEh`j+WqyeH5c^?faQ'
        '5Cu~RStwL}TaXKeG?wC^6;BE3J07_>NQZ@U;u+nih7+d4es2c66!dJ!Q=yNAua8_4z0CWZIq`mA6YwOk3wRND9T)_T1E+zrfQQ6XJd>v4'
        'DLXX<z;kJ8HPED!!h>SN3n`Z}DV2XRGzpQLv>kxINuL9-pEOKj9qj7ir>-18gzHd7J)XAf`v64RiYH)eF)$6d6F{A;uaEyMIhcQbXrRz+'
        '<qJA*H2t=p3s_I<cxv<4@t+6S@)MP`E77M{S1V9$wMcc<I;yQzJ}+->5UAN9QoTb*^(}g`wiw82H&U(LM70fOn$+M&lkEP~Xb+&qra)?I'
        '3Zk}_U}|j%q1Gk~H8q7&Q-k8G-XXw;2p>B5(8C92V*}Qk1`~Xk;lmF;{NW=2J_6w*2tI=0BLqGys7om7qJE}tM_uhA>~*l$!`=XUBkWDE'
        'H^bf!_WrOBfPEnBgJ2&F`w-N_f_j9a9_r60;Hwe78k=;m)x*{RTO({uur<Th54QfW4S;PRY=dAMj5>s%4i?lQ6m?L1H8u(GA;O1_e|uSL'
        'lL0=A@L_@v_6;F^@Zk?10q_wBA3^XD3?CuzVL@F&Q5VHmYl{GT5%xORV@+>h->QW5(_w<W8TNj#_lJD|>;qvR1p8pvhoBx7)FTx2P}^7|'
        'P)&_UlhmJ&v^B$bvk3n>=<2D_q4;j*@l(r3TN+V*Bl^DCf%4IoHngR!*#uj4e0ba2jP^Dw?QLvg--=@L_0^<Cf$ADDuIxIpqu=c)%ZjqB'
        'cGmBTP96Hdjy|xXEGx>gqCY30KPNSsU^~|5lb|!HQ3shGHU`*Wr{dq<qUaQ63RI9GQenD|3exmcm}a1YbR!jJn5ZCA(J9Ums3cpY(i|O?'
        'W$URd$3UgoMk>iMQE|4agLsu-E|k_lry4pn(5Z$_4Rop%ol4A&3Y$okRvlH;>#4HdKowRaRoYBcp^k?_?D7xO{;}0jfk?iOpE=ish3P0U'
        'N+)MT8->(Vot&4glM8e7a!HO+EXp;?C3!}<s7xo9ROsZgN}XJVw6;=|Csm7bZ5>jZDAzZLI!A+EZUtEBF~NF@wCLoh7^55!W7Jz>baH62'
        'PL4=57$#2C$ps7<tQ^r;UMg}MGO+)y&VD-OAHbN-U^JQiz!H-rU4-6X^5b$S7>ueWG!m1{e%PqRhIAksC<W?(cA$quz1NGFoHB9D&6qyc'
        '!iIHY`Z-uDbIy>Mi_JdQ2|NJ20({QD=dzPl(^K>gX9TtyJc4RzK0Qb;&<V~Wz#asj{~h18*LY|0_m%uc^tF-*yjeNkyNt6hA-z_8BDfwy'
        'Cr1QF=2gDNRC$Yyh-oACmqzS+jo9lN(GQJF0PHo5gN~-=tNv^6aQgmLR8&+}R#sJ2RaaNn*VkLER-4V%(9qD<);4+a<o5RVj*gD{XRq<z'
        '^4;FD{C3qFTi@8Qf8+j;w?p0#Ub}67(c53W@zws&{rsnldeZ$yd{0<U<XJshGSV?ZBORk9p4Aw7>&5qUzwxHm(yOYks4uE6sLrb9e_MzB'
        'T_5(hZrI=DVShpEI^RJg)8F<TNN)Gv_8my>kiYFaklgZ(e%FOI&wj`BvirVoX;AOGce^jkhwQ%0-X+VI>090I%k-0Em-qgEeZRtgeZRv0'
        '$KS6Yig*|&VXn|CEEjGQrU+fZHwkYFzYBgi_z}@T^+LAr6j_6&3X8}V)I&+aHsOB?NA)%0A4R*+uTKv;9`u1Qz`w2J0s6IYyVU&sTNg<7'
        'g?)VsZ(g~0#T~>x2A3SR?wQ0dPFGilt(|{(rrhETuM<5#Tq<=g{{9^cmP?IGm#kR0Waal)EL^$-7ET8h6%<R#kCE6<wiH5Vx|gn8GXHjI'
        ';S$NVV#V_B-@HQVSUP_}Jxpk(qnqTe1-CC)A+;>LrC;j4dFk>6QqTA2EgrkiU+?VReAj%L*x>;u1leTvmm_55q|k<EthOhBr+{aGoxrZ~'
        '-)C($`{5`N7^Hb}4AVTl#%P`#6WQM^qhxc6In|tQ&NOG6bItkYLUXaX)Ld?^G*_Ez&2?t0xxs87zBDrG@%;@2{(Qv$4w>xcf04ngN`x_q'
        '@<$zp%p<98{`rn#9Fs|sTa+xwKSY1V{nVitH$VG@ye`AzNU9|hdC9A9ktp0P#_&H6rTAlc?oTHlMUm2oq}&(0<w?qYi{il&qx$pN2fIbm'
        '>PKkv7K!C!xIL4M#1EO>;@DA@=VP>abBvq+jmS{-e~Z9AhW~d9aq=7h=ZwiXXH4D;;QTfj&%*8b%I95U9};k`_x`rDAPOWJbR-(hdciNy'
        '<QE(um_vf}hG2`I7!(#57#1Y@hlUu9A))?mz1!e6x=n7g+t2Oq4sZv$gWSRH5O<h6${p>Fb5C$5x>F9V!~1@IU3cgKHGK%!bZ9fK1HhAq'
        'whz28@J|CT4*c`LO9L+tyfX0Hfma9q$H3l!eFLu#yfyIlz=46^4-5{xPdG@R<s&@RtEm|X6fC$#05L-RwsqU?+jjr9pKM#dZNs()w*7S5'
        'gWG<#?V)WOw{6<CdD}0x4QzXS+mqY2Z(N7>{rtLa;{$5?5U^?EW?Tn=CpT{2w@#3RR9rKG+<ohCEfh*|trTi;wE}jbh5gm%hwo=sw|>(D'
        'O8Qu^TmSR0%~5XsFXG&Kp<U?wc-_bAgdTQ9Izvro1HHn0T>F8=!ZQB1HQ3+4)8m{@CmvS+rX~BIMT<#v74y|a>|`HNVh(mH|Ie3b*~`6E'
        'y>7hUMf$FfXYt%Zo^Rz6cdeq~u8n0^q*=h$-p?Ri*c;osp?51%CUYZQh}>45x;e}0{S`FXy(;9~y?69}!2P-vUyyC+eHj|9NVB-Vg-U%+'
        'F>lrV?)lYUxclFQPHXRkW$wlAo^sbdzU)AMtm3uyz!z?w{&d+^p1N!A;H7P4f0BU97&h?wRq<N2a`rC2x|y7}gS9-C^LKe0ln5;3*b3QJ'
        'wO(#t%UV%`cTsB=4|b=OM{xu9@d4LTV(Nz9mwj!F?N#c-V_CaZi5q`!fPQOl&N9ZUzxcs27Vpnk1Qzq?opSe2X6>1O;EMzOcl06-Y8%J;'
        '(d$!bFY~;Rw{0Q6zYyM8&D?4`ylwnU)5cbQ?_C~2rDd#_V|hO+EpYQ3)7;AazN@xzgRhNBTv^?-AgwyZ%JueREB9K%`w@BcBP)Zo@xT{V'
        'yp6wK7W=JjynL+2^4?-?UZ{@04Qd;+)KSpNBk&n-(*|C<EY-(CwJi&I1h(>i@wPFG%kKcs@<ohku>Rf9tMrx9M%HIn8>`wjvRW!_d{=Gb'
        '2DOdwdy2*Pe*p&y4io'
    )),
    (0x2be00, 0x2f070, (
        'c-mch4R9URmFDT&_q{&vy?*cY`;lbHveXumktN#*5dKH6Wy@e2V;he##t2(r!8pco7~^GGhLE=7$#}ACib_#gDwRrUy$r)J6y-^=sT9Q~f)JMR'
        'u!O~iAq+z_0SuFwq6`?~fU);G_x6)y?E2p6b5Gy@bMN`?ZISx<?@0Y>9jX8OV^XiLCiQk3srMi3q|Tr-?Ck5LbkIl~_~8sH_mzVZd4W$2R17*u'
        'g(2xsg{_nhHS8Rw!zyJ%FDz04zj+Em4g>PTEPk`0k6#W8_)Uiaesx&En+hpqJQu=(vHEzk5Hd>0haO7Ehb~p|>ri7@MXpA?S4NI9Qk1DFMGn$b'
        '*bu59PYp<GLfy?_j%J1#B5r<)*!vNwXMa!XxxM43oL!|-P^K|jLFyN;0!Q%7GxihnWq9L0;6dOK;Ag-~zyxrb)Grxm1^R#?U@Nc(h=GTI$AD*m'
        'SAchcGo+sPfe`2iFuu<Z19t+4fQNx6fN|gi@BwfZ^AP}@z~b}giQA6?=tukafga%Rh-ogME!sO1`4h;E70>e`f6&k8MLz3$6{|N(diq(vlD9Hn'
        'vC?a{vR&--tlylsvLbduIn7r0+O6IWd+l?_Z?1?O^efF)&$o-5?YB$%Y2+9E^Z~yk@9Em<TXnueL+_xD0Q!d`IuQNCm^vz=D_t@A5$b4!{z0T0'
        'MgL%eI;8v$-qFn+BL62f=d~tGSiV_gNUg0%e`}1|+p}3CTeVv|8BuGyt7|7iYMbf0wf`8SnQbD+sTKKZACFRVtH?)aev8fD+m=ez-ui25nQ!&B'
        'UeQz3)l~{apWmM{>5IEKUwbR0T9+=0zM{)(6uG*~Z836r+O54sbj@-%C3@E)TVB_d<&@;>8mwre4>eeM^<3A;H8XQ!zh+4CjZ^phEaZ3mOXo{F'
        '-p^ioX)0ZN>jS=Yq3HjXy<(vU`GLR8>cwSCrG2}2S!C_CsBG*(pmjt!b^d%GnSVN$W%9GE{2ZtA8P@qMa~{uyEN2Tj=7P_D5zoaO7lIrI=g*yw'
        'e|?^6lKK2Oq6f$uSK2-kou&KF)}JS6;tO)^Gm-Ngsrs|Yd!AAh(>EicHb^2RsR3FflI15RM?^@0QDufHj^!PsF$C7U<ccHcL=R%$AXcHt6^9g='
        'G!;>xqkyK;@B-@NIHak(Q7okeG@GXCfC-HXDVS_aB$-LWkhD+9kG#N9((u>_$Gm`dm9D30$5^D*T+e&N_L?cFQ0h=@+OJWWNt0$OJ7m%rlUKTP'
        'BpFt`W$M5tX<H!0u*otlEs~ZvU&#wwOkb%36-4xvwxEiTzS8EYahdF!G?~9L2An9<#wrLf0qVjr4Y8Fg=fbg%Oy&#c2!-j0E}TmgW~Xx6oCnIW'
        'Q$Ec%I8NMZnhzy~P4_i~j;XY|PddR=TH>E^QkY5OX`?xr7GQU7o<ZuxH%R@%L*pN~56{e(^7IC6Aoaos;JdFQ%G^&p6OrH_o&=5oCrKSs03z?P'
        'rN9<oAMickao`02QTo_9QvcWjECe<Idx7r)j{;b0|M)iWIjNT#0YtKw)&skN?*K=Dp960KpCQ_pfd#-?;CA4kL>gL-DDphy2vD?<rTsEIw<XvM'
        '{TbkY;dvPHzbE!@*a$^;LH-7)0xv_qx{zx~)y~FL^i#Ta*3r#I4(fEVvs63#oK81sgy?a)sL3<3P{OWuZk%Q$GSdwC;W$+iIm^f~Dwb1j?cCq%'
        '=0s*&AV0iLSB)&`HY5L^_S$E8vKu#b_}Fb<ppK5FrrP0gNbLScC#b6<2YDNHbm?5}=&$sg7DNfy=d@YbWAuo6+N^BpPUJjmZ|hEEqA%-7<O2I%'
        'ttXKSY<l-nE9WJ$FOmIA?R$d@y-aO*oEBe}^=gNSdIzMvh)xoX4*!xC^eQFq^)3)OM*VYb`l7j_qnvp*eQv*`ucWz)Z2G)@n{RLx`h7U2p{3fd'
        '4Zom=Rw3H<MRfIm=33u4tsOwrgj|>C>sDore*G$45xMCmosvK>aE(rh%SYFsp1xPqkdb4w>Ka7tzO}S^y_EBJwASdy=-O*U@1+gbV4Pv!aGj)&'
        '(=|JSd=2Gn+u=gKOv5{x@&l*GY4=Xq`y<+WM+*8F-Mk|Y`D-2+^tg7<7!3@f9fyzWrOO%PHbH~Z-g`ECS1y)*6D|9#UELCyUjN(Fuj|j$UHe6$'
        '{!HWglV6Zqk6zCBjI;N_i?IcItQ=;oAQf|3WtiPGE7#++%bayH?CUfaG;|GJXE;cuxt#Jin_>>q9#_+lp2v+z+4RLJD%PW6n$&$$kx`wRdUhhL'
        '!(QxB>DoBeYB7~+chY8zxNEl>v!zPpazupnC}E6_nGmQdN<v9J(dKpVdI-)Q);C29*${&upsM@?lhMQ2z*{jMV3z@YIN7j6O7?Z^_pA|uF;zO*'
        ';B8PtsMRP_AsMOaz*K~XO-Bfwj>Hd?hFo?hWn)t~Fkr8yYHYVx(+L$An-k~<B|wwe-`RFv`XnO}D3(Vu@gRt8^Fuozp`(-CTzVvN-wsb~-aa@r'
        '8j&FqW_ToC#5f>KjYc3{NdjPimqE{|I~fh3-;zu6gpoX2IuMvn*pM7bTSCXAaRMdnQc^zp5U8L&N+y^}RB~+ZVpd$Y$uK)QN~RQLYNt~kyAzwd'
        'X=0p0TMnTMNgt#MI<V~pp`l<k4JP4HmRbNNWxK}`$l|&tbc<D#yorwMFs_#(?x0PCHf~{pXC|rrkYtly7`KQlxiF(#jUvMz#EFyGY|4@1G~5$2'
        'f`1350<yNv&|%_Sb>b3R_mb-2Q&O)}@2m;X(iEg?X<j@@G41?G7f@c+G|!oC3s(nrnOJYvG_A}?=v5@`KScH6uG`{J+kuUuWZl(wAn`2wsuPBj'
        'WMNV!>sp0%#t}`~ICz1$1)SiOr%Ao~>i8?}>c$K=Ay8gT>g9vL_DM#8$o?wMkH=Biad431IHMj1%Qz0+ar_<tB<VQLr^j*DJPv+x{1mAZSkfo3'
        '#!oB;HUhf<4EYJ1OHUjHUIcLVJ%MxVYghoUbps%lui@PL8qTn<9R|h#oReR}nfEo+|2pb_9reH7XBdUJ|6;I#0m#jew?eK<n1&BL9h5FAML5p|'
        'rP5sKdC%+&*T%7GdpT1Z52@;<Qqc@-hz%lRYRI{{i2hYKRkE(E-fUwI@?WVu!_7u?M28g@=g5#|HQ^ixrZB5Xbes!k`bO`bCCp%gdfFxZZ*+OK'
        '2c4*~4G{|V#x~JMs3mlL(Oc)@Jcjq%dL{jDD4dN5GmiD&C31x3_J|y#OS*MnWPeGET;8wKV!yc0GkQ;-&P#uKmXwV?u&&x#8;WSdl6<*V9iwGS'
        '$_23xTvg1Aea$*AC;GY#h;%~;23O)7IX<LUtQPqQtyzNdhQ?^!)u;#db%ThgLn&{A$a#OQ*DV2)m`Ai}N!F~_8`gqhpq?Aox<!%KUzala^*392'
        '-MUnh$eXsLjXt`?bqCIdG<v<OM8ElF(IJPezH?aXfp<f?c_-4}9i!o$%Gh@pIYA>^q}(|)a(znG=AEP)O+ApqR^I`A;Oy<RLv*B%Zgo=wXXogh'
        'TU83WzIlh}C-v|S(I3@2hm{&Q`xp)1mdXvhi-7pa<hk<Pz_-D$ljn)z<!2_NJ>8$IP<eidX1rg2j_F&!6-x<mt<0>-8BSLa?JJ0O6-4(co~wAS'
        'X1Gwza=9VRy1`>^!11^t!_|f?&uB!nZ-kB&S#8X6OJk1Pnmi7gkgX}hElpVtn{wP~>zl}&`4+Wftu_*D1RLde4*A>zrqSwTICQc+$I0<L$LBsU'
        'jt&J~L03Lsrt-WHtfSk_@;o=km$^PKcJsVkXL*5!uA%EZ59k7~NM*S{mE*-Jp9fNT9!eE>bvnmO)6mn<(*<6eF7kRW$C2mrkeBDRUV%4yMc&Ll'
        'U&YWFIv05h2fQug^SVr)H)aaFB~#?#Ou!@AJa5WE&qB`zd~3GEx99S_HCN!_T#;|h1-vU);yr$WxBJk2=zfX!`eojiFYsu-$h-0Z@6DHZf4<E3'
        '6pFmN0KEXcQ099J6~4b%<U5N2?=P14-eQ^KAmF<LJO_9#1$<wr#BsUA2kY$VFCU|CVb4ipu>TZ0$->%3ngQmNN@B+3AukEpmL51XdC*J3wV?*G'
        '_c~OnACQd~vArPBiaP3tX-Yzlxj4}Eom=8u;205`!+$)<#iCG2*hRf@9K@W&O=*j^>Ded>BNK3fD5?B_30Vk9p-ExW5rk-zhj0bf7Ff0v*x$&+'
        '<cNj0Ioc8e?DHI&^q4g`o21$3<{S!4IaE@@*8<ZO;SS2Nu?a!ikva`;GW0Ss;m+`qV1;eIxg@c4OSor*HrwXQMTw<a!aGB`s0e9_0oh}@ysPx&'
        'Jm~4^TCt{SG`C(38_H<@4Nk8&AXP7DFr3!T&`?FwpabEwenqjJHJPNrY2y$l3`-_eO&_<}^|&5hIL<*rT#~Zt^CDM2AUnxmOwFTQ6S1OX@TTTn'
        'D?0)XSz1{4lRE!Xl#G7jS3%uzEghWn!?5n?mI_Y#Z8V)>=qW#sr?V2Cwk{l+VlqK|!8)2`KQbR>MEIHD9KU&j)SENLKX8L=Q)7nSp!KBwX*s}k'
        'rZNjDZ!Q4V0=EO;F>n4D0LOXrb>L%C|6BlifUAI8f%}0U0zU;_1wJJ8Ru-58tN^wH_X0lvegeD<{GQam;HdR49l!vv4Y(WlKJX-P3^+;ZZDnrR'
        'p9B5}@GIaKz#0s98WNy=!1c);$3oyz;HoMPe>hR=hMabVc{Ju#Dxz=e#*)g_-iWDjrZP;S+MYwi2XpBFBZ1!7sa(-#c4$=_pP-o+;Y>b$NY9+3'
        'Gqop>-rJ}}&hOR5+7m?cmf$_;y-N`(p8#`NigJ&>pfe3R9nrJoH@av?2Njx9SexIba#K!8{jJcYp0GifNJtmqJ*mH|2@xF3By6$uhO@L^djgEB'
        't4WKzyh}Goef>$j{YkxxO}&rm-em>6_ZPZv0Qnz^sBf_3)gBtBft6rN5B;USa$eDwe5)3LxjZpKtCop8tXI`^w)SvLt5>CT?O~#ItE|56I`k7~'
        '_6;|sQ*t+P-Ax|kztOgvz^o1()7yu^91cA|w+@5BjK(xN>^9U!Bf5RB=tR4Ax&_g9?aV_Tp?y2ubZs=GKiiec$@@#LKz@wlk}JUYAeSux!$JE7'
        'mWVz=ORi+2FS*j%7hZw(9t~;H5{%Q)n3mP>{!!##DH8gi<o~Y}4Wj--;}l814?Uoxt6fuHv>x+^cKyj3HzWF=tYOpc8`kI!BX?PScog;Fz2VV}'
        '(fbF}SWhE##fp^K>K3llMlKq1?K&&sf5L3MGwvkIX{Ev>6FNg@iE=Y0lKTx1p!dg@dp1J4P${{07?7WP?DHui&mFhV*a6zjO}&T5cI*@2n)GNb'
        'PNFE+186W6NzFD8Pe#L_o;bOG#Acruo4RSHsUx?!gr{n9ml_jIU0)}9a>#Pb9%RP>$J)4l<6+O0l0Mtk%1N0{60Pgu*~Cn-W$lsM+zq2avIWYu'
        'hp}xmw$SW;(h)hKvP2Zy)Qr6b2+RP;(NRlOF)fi3r8(`e++xd-Q^jOsh>Ajc4WM;w-auQUqzBr>g3y#EcOepgBXh2|A)cJTv_kGsLfcps+iv0='
        'MnYijULu_T<xa(L2`w>Nn|p9h4(TYK&BceDqe(bO*kSK_lr%Po>*q~${UFX__KxU)1liEq;($@2b`B?ZO9$%rGAJ`L^niPY5<gf$vqNhOrGw}}'
        '5Osuh{zB71+lN5nyb7r!u-`QT(?Q#JmH?+CXNZ$83uYYbm}v=e!fBEYkolHi$67gBW(ZMS+Y17g!@8Wo9f@9!loL8;mK?>FgkbPuGj`^K14dl#'
        'IRbk}WIu95#)%}X%LNaImOP@oNM1M2=R_<wNI_(M*DQ+Ac8n4(6(%2AWQ<bwnjn5W@zyA;pZU=od%2UizN&u|F>5NamqN+CM8Xv8U0t0cCFCk$'
        '3zJdZEYM(baq0^qK23cF2`4|sA*w%4+={6FyYG^Er{~$K8-Me@Kfj+oq`jnm+k)8n_GDB?#C>NgfC&4}Lja=iJFftU)bIKLqWQb40Yv<F4*^Gj'
        'ao}y>EUEWU+Ix$EEx>*NQTn~30Lp#uKS-TKv_IJmfDxP=1;83kjsZUh-U2=+_5KWCA+Q<PXQKLVY(%Fa$n}t5DeZp`JcQ>y$P>V$z}+^&Q!a_}'
        'E6Az#%8XaqQU#ft>`eI(b%gQ_F1+D@&bPX*j8er(wbo9>)I2+77J5_Bb48!U?d>9)I*KzzULsNc)IqvPqVB09bkQtCfm06k%u2g5t4(dLl3T3i'
        'Hl-u1)uwjW7ed(7opvMmW=pr5j`Rq%^ol+~&0rIe{w1~ddXR^xxfAR|@?VU0Ab;z{qC@s*qc`=qMmG1@^yc{<^bu+|_7ACjzQ_V~&FPq3I}y`@'
        'OVG{}t5nw_k$<6j6M1<emnHI&Ic`>V_x`yq*G?SJ{e3P=<XN`VcWWmerAwBgpC=sEwKV6-dso)1erYZ5$`|9>5Tf%5k5&$W{X7-ZRcpXfAXi^4'
        'I?>8aU`^1kyV}ZY6Mfq{U8p@3(dJEHH+XN$4QXYif*#VgtyLxO-Efm?s691CtB3L^m;LM3i0t-PZpc7BrdMt*7<ugmu(YQR>l?PBU;AU)dSj&`'
        'a@$SCVr_3sHxDZ%`Yktu73?Rl5wJMux87pYx9?OY-}c+kj{W1bZ3o)9|8HsQ9T~3eKcGkM0Gru=hZ_Bc$X+#S^xfWw(H~bk{*3+Fen)M+GwaLR'
        'yZsKgv9^DVcHhVu<fq-Y*!+8M6PZ(cZ?XAz-)8gQIg*!p(%-nz&r1H?f7*!pBii@1nZ;T+Skhha!|sUg`_Eu2(C_{x#=SeFd;i??YTc;kYhW|`'
        '=h3b^L_VPRh`uMLU0)YDMtdZE&pf@`=sxWc3AVNO>nLaRTH5mslso$0^uDiw)ocbEy9?`J^91d^3---lr+s4I4EtTkH#A23?}8r;4br~<1iu+_'
        'wDZ5Goh!gz{`r&qKTX#c(HBqF58k572a(3}Q@Zr|zXW=kE?#(Fak@_Q@24DZy6)2XFP=R=r4yZT{?8w4=coVO!T>f3>?P}kO@IxlL>A;y91`50'
        'gy(MpODfbNbLUW}Kh9U~9OC+Oc!5<1$w@P@M)q2~e#qi!_ZNvZn*59{;coJ@mXsWZy8ed&2$XR&)dq964U?mDBq276r>y3L8|lfi3<a9BBG+sk'
        'oAY}h1SGWIMCu>W1DdVush@>M7#bv>ACe|Xgf>({z8DF!us0iy{m?HlIwBLHm3%BvO71t}`o|^{QI&)~IpQiuA(E>4Dc2nA9c^N_IhD&kj5oq?'
        '@`#FFMK*qdHGncXK^%mWWK#!`+)NlgXs8?~mj(9gNEplg1kTT$A|qiR;p7Y}!v=(qD7mfBQ9X`^$u}4+bvpL=E)>j`T>nBNb%Jp?7h2<_WEm@v'
        '2w`*6O13vQk^L~{P-iG#{3csvzN|?3j`<+NLEud5(ZOWmWk?+jzWd<hXvF3wMQRVkuFNgQ)JQtXpIn&xC&yeMMN_k2C}5DdvNG3BG&O^AH59Tf'
        ')c%dd^x22A3<laZ$kLnXo)f^YSc#je<gRSG*P&(Km+KXiEv)~2$mB}Fz#WiFH$zkFfAv$h9s*NZVY^$u|CqK?4%)^k4yWCMOs<Su&|?{WJ#`H-'
        'ZH3N8k2xW5bsw8r-I$aIb^YsC{bHxSGjL@$apQfY{{4AUr?!v3q3+BDxkh@Ct|0aMR)Fe^YX*Gz)SUph*{MeWFria#03Vb32#o8aIRIwpqip~f'
        ')koh09s`~Oz?eSzjMT>%%#Xp$J{|(L1NQ)6ULQXJyZ}r9U{;@0fdv2<+$XmJXyGS60Km{bIR?B7oFjD_4D9q}z&Zd-?=+_E^bdij05G}JACUSK'
        'Yy4C6<Wn%UPd5U)0WiW(e+>K#cohKS`z#H>|2|s^fYE&h&iUD40L<~TXMop%Q>6X?X84CrU;qFE{lh-s0pL*p`}H5*1WuDWQvkpq&#VB30Wj4w'
        '4+4(^80|A)qG!&K`ePZG2Y_k*aRj&*0Hgimlfa9>?*K5?&l>@7<ImRuV5Fbl4?GNj`F{QqaFWy)nC>sYY`^FS)&si$FxM}B1pE{LL;m9Tq|Rdb'
        'o^1obfX{9M_5j}jz;Mq#4V(bLq`$<Rf7t;n2f&oS#QcAW`Tr90|0U-COU(b5nE!K_|8toCbC~~gnE!K_|8toCbC~~gnE!K_|8t)ihWsGBZzZq@'
        '=!Gs{=jK2@o-L+Qa+oOuuEaRXHY9qZ=-9RL#iD$<tCqYp7_n-sxS0str8-OgQZ%Lp`HRZ4h|=xCl)*$z`Z&#Mm;17Vs-rz+%I%z;cct6~7kTDT'
        'wBVwY$-iK3qtW~3S518jy9&@pXi2YY?%Db-sd8;1rpxDpVL<lvYs1KwUE-R*O{rb(nyciM{cgE75z)$JU?3BRbZb}Aj*F9aTrBMf<%^zaPj{DF'
        'lKML5qd$;yE|D*eF?BEUiXwZK$$J;kf<^hF)O+cotm)rnSEe%N-ey$+?EwQ`CG9$tTe+%Gs7*}Jnl)fP6YtSg>#SU#$h8|qrs&!Y-QBfkz;HK!'
        'k)hn3o6_mp1kvrgQbyl$yOcKqDg6j(>(*3G?DVZV$7w$`BY8SaXHxaQY&mdVK7+}V7r3v~xz4$1EQw}O77-%D%E_`;kSfb*m1FHPD>uVw7rL8c'
        'PX5{@t)c52`zenL@)s{Dq@}X#r=h2r%jpaUY3N>>3!cYi4{2V8tDet|oW|CLG)5X{xP^UIoafd|irX@24l}T2m|HSARvDkunLKx7VaukuGwX4f'
        'g)IwP7Pf4j=VS}qozvWwOL1oo@8mr0$z^$7F30Jd&kJ&S?#tmFU-M-?-tqB{?{UAM<AuJ@zMtpCeu4Y^A}`ImygaXYAdmO*Y3|SGILgDGFYt<d'
        'k%tPf7c}>w(*x*ef1$uv6^h(h2zaQdczMy~z9Q_!6t6B8cx@53V!-PH#p?su0@#8SZw!jOHVAlAP~y!c#al|Sl{9ZG6?tnZ;BpD+WtX><kyZ|P'
        'Te-yLa+!xKE^n_uuatPCQs&lbiEpizIbE%AYeR`g8}Qsv;f}^Kk2Y3#PGgn3o6>xHQ<--)Rk*JSx}Eh%Zu(r_O=)f;kB6wjTd2x?)WCZX-uF5#'
        'w>l}l)4^ytusg6jRo<u+?^7<kLvyQ2aY3bdzw)?KF%PLKZ+8`sy0E*N?{-sMcGG;13p+gNUboEm!KFsE=KD2lI?XNG<F)Xr7+%$_%Y0B*_)yB_'
        '@1!)BQz>qNhaHB8^})mTq)PnVRGB+d6@DPCd0QHGc-(S$Tpv7cW4gq5reRN4fGU5_)BGSj@cZz<K6v0pFMv0ec)y2tybAZj6BRu1A$a0OhK(`G'
        '_`jcFgE#&F-k63rKAb7?4>OpR4D6XQ{|KHq3QvTCb8EK9Kh6d`mW3@_<`#J1C_FHY#c~8*_eieDkLF;{mAD07H|isH`x)L0k9!Or_qZSMqkf5>'
        '$b0-`9xEcB;iK@lpTOgug2z3c5BR4Atf~TR1+11ro__}AX?WdJ@VaM;Y5sW;E2hXiUiA67VxFIe=RFP2dm-?6TfqEckl~@g=VL*hUkVERJiPDa'
        'lE<%<m^(|bm2$kk<nyZ~*h>XIfu*??OR%$?<)N|<FU<3+<pRH6L3#!0l`OweL3$<6Z&owBwwmR)s(Ietkm0u{y-U8T$ffRI;dv3xM;hlN`u{gL'
        '0U`'
    )),
    (0x81800, 0x81a00, (
        'c-noC%}N4M9L4|05SNe$(!R-v7$Tw%k&uXPgoQg_U=SK&R+qUjv}h+p`=FvW7jFzQ3^9ldgM@^Nh=~OE5qt#ykI>LEqTk{iIES-2'
        'X*+$FEGCV_e@y)k{7E7awc5RbrfK9Caj5};B+Nnz7GV|EVH37s2lgQcc{qksC_s?_Nf3=p#e->*Br{|-rKf|8$OM^aPwagigt<Hw'
        ')TK4*QJ>anNSpMXc4?3H>3|ODsGy$*B~c1W(Pe15SNSX7+U0_~yv9B5^EwZClfUyW@9{n#@F5>nL?x;WZiCzKUOaeD`b$<m%4aEL'
        'RlZ7B)}$wW`6dHdmkk-p583XBj^AnZyuSXKRYFzOt8!IMdCFIBDo}ORP@(!z?Xeg~<L*RE{7DPvFokKH#|2!(C0xc8T*Wn9#|_*>'
        '9k*~BcW@W?F^f4o#5^A1F`nQlo?!tmu=sQDjcwaEU^`&jju9(3w!^?NnBzDs4q|5(FvDVuSq6jHEz<<kw7@8rt#aA00IrR1U46JP'
    )),
    (0x82800, 0x83400, (
        'c-p;Ie{37o9sde7(Gx;bS1RbH3YYRzh~-D)q%A_Z1Fpr3O3W&&swyvX(re>QQO^Yf(G<<6&SgglIB8y*OJ;hf$(kx;yrbM8M3((R'
        '?BvoJ&Vf_qO(`}sTK$KGpDL^BAC<P}02_a7|L%L=U!U*$dG~$q-P>}jrL|>eOIwT3GSD*CGS`x7$+eVPXe(;n)w-{>uXUg`)Ow~B'
        'x9Y8_Zy)%tboX69B>11Sb;}Ql1-1Z!dtfVUhaIpJcEQ8&I6Mg*;D&whH1HsR2M)oD;De*!hX9BWgb@fq7)D_XWQc+S<1hgjREUEP'
        '3AhAFFu;U)NCSZ!SWti>I8cIhsK5qPp#~Hh2+&<<3u;9O-Hq-+KSEp4HnbhxhjyT!qMhggv<vM<52HuX<LC+WBx*w)Xb*Cu9<&cV'
        'g`P&eh)2&N0Ubmh)Q1kCe)J+bjC|+_I*N`VKN>&*G=xNS5(Uw((Fi()Lg*zFMz5k#^cysWev4#u21U{9NI~b&IC>jRpz{c$3rIzC'
        'D2^^79sLm{(4WvH^e#%G6f)3dWTFqzJX%0$ltBc2f^ujXS?CHXpwCbdeU2P-6_wE6(K`C_$JVygHgj9%UUF}7yS}}0AAeuze(Qc`'
        'N5>BGlfce~OLj$FiYxBYT?tpxWx5tzX%}(jT$Zcoa$F@>)m3v*SK~qJLApD&+uWUbsQ5_Yk&Q?B$E?Rn?Q*-)9&b;y8|~?K(r&ev'
        '+N<rgcG}+PFgkIk(RsPk?40i;omOY9lkQ3FG52J;aF@|#cFlK@u3{JMYPfkfcI)nx+i;uiv^(P_?t<HKm)sRMbvL^CZrq*dHoDF3'
        '`EJtfbeFm}diWmPWAvCk=^m$t?ltz3z1Ci5pKqVD-?QJhKd|57WbO<X<rHp$!<@>^a&ugqyU6KWg1f{eIfFB~d2WGAa~Uqn5iZB&'
        'Ig7i(6}Zp1BDcml+*Pi`t#jA73b(;kxf=HkN4c9^;~DE2`mFgJd9Env2igww9^{`-KW{x>@&r8O=Yc~Fuk4L_6|e41c#~e!yWk~W'
        '%UkkRy)`fOHU{yaIY<Vr!OCEDur^4C%;WfY>bQA4bDSJ69;afP*emiP7IiTtnqpeah(s)i6|r%GKan_Lo|r#DPB<qvPVy(slj)P>'
        'q%)izCd1aS6C}Z0u=1;bBui0AktQT8snVPjmo7@Wl#nh-?@CE2B^lCX$&@~n=A{KGEoG#vM5MfANd>7Wtx1k_RVqpA(lx0feJyQB'
        'H>9dmlc;o4YDl-F+o#D(W;hkjyvj%9NHn5G^hhFNM9fG!LL#|{6>%cfNG(DmjWg!&icvjUIoo!&_jUgFX{AF6Dddg7xk^lqDKR~k'
        'h$Ul2%#1C>NX&{AW2IO%R*TVCV=_94C*zamB$>1(3zL<}>SS$_o;RnIDLfUQ(x*~W=2T{iOj%Qfsp1sHZMYZncpPIK$2v}71DiOF'
        'Gnilt7qEjXxG~L7PfX+K_;h00m^P=$v^8CtzI}nefG?OA$c6O_cV^TXedf}PIg_3tGr5`kj5Sl3S(|ZYDr$!sP(?MQCe);AszkL^'
        'N3G6!W)IB<W=Cd?+4)&(wm9p|dFFg`=3IKt`2&fQcrM;}C;X1}PD7KmsHSM++JuHRRh!e|+C@#*651v0T`j4-r=>JQyR4bo$J&CH'
        ')-qaFBU(<gw1QUD)-*@Es+F{L?V9$bR?)uJZfI4lrcv#t*3fQgx3xQemft7uTbIob$OlC;WoAtBm(qv)N9IS?ypV2W<!m&Ivuak)'
        'CbG$_ku|dm*>sj<b6G3vWJ}p<ww9&Y1~JGd<mO_0(OA5^XfDn#l0|E=u;?t-78{?KOUe>niZ7*>%%%CI%o15DE;&my$LHcXJ!j<1'
        '9LW`OPLAdp`L=v-p3hI@aXy~c^G4pxr}HH5<V*S6%ltB4jxX!W#<IDbUUrt(m+A7I6?J8HCB9;;q*usFe#KfTtgNj#E7w*kD;rja'
        '<+J>jXoW1(B9>)2*1A=(s;h@qeXIV}!0O0qXtlWNtgf$CuK2F_uT=hev_J}tPs5)!Y}t<5ialXtTeauxxP8&q?Sy^FPTKF;hHcvO'
        '_Q&>uowhS})+Tn&w(Ki*!T!uH+H1CBU$sm2x?QoWcFq3AruI#{Vc%IJpPOH#zQ{PlDO}}C9VMYe{zliydZR3taak?v<wQAIHp*ss'
        'p`0$0a<1%@OXZDnwOlLHa^oxVmGu?<+Pt2+p1Dr07dHz3;BS~Wtg28YwMJd8N9$@`uP5rsx=}al3-xrJ)SY^%Uai;awBDdV@1k31'
        'D@F7kx|MFHJLpdO0Nq8|!Au{b57S5KWAt(Q1bveJjJDAZ$_`xGMcuTUvO|*Yqx<Po^l92lIm**#sXz}<5ACCeXg__C{(>H+K6->6'
        'rGENLIzR(-h>CQW2I&YrMMLx@8m6z(Q98Ey`#u!}2%Lgb06`rf6$(Lo0@xoICX}XP?u6oJ_$@_w%kNjt`TghK@cZ9@^NMnwVHSJq'
        'O~3z5P!&aG*w2Gs1=iG~ssOy=@qntTOab%WR1{WMX?8uq?Aw0-+c2#t(@kNR^>;qz_s9Ops5v!xbQDHMWoU8}y!M(b%S^rUioEGC'
        'xCvf<xrtwChBv|K(@p$xGyEMm-PEUfU@q{oJUrZF83F-05CECEO&$*LazN$-0iF#_!vK`gEk>JOI7~N<ADWPHcrysi@W@Cw+=QVJ'
        'YbJzgGaCM`MzfLt(5%p8row{2I>BepvIO9<ai+zwO%?&hn(e=pgG~CE^fNik<Oq{vOa_<?g%1dD;Gh5pJ?u8sCqUl|0=&>KK>uL@'
        '4j&QV$T0zq4G1tWB*4&sATTQo9R%TE9|(Q@AoL#w;qVa<jvNEw*Z{C{5Qe`0?!X^q*EBeKl>ZhO$)?3>(tu6Ylk8RoDu!fI1y+c$'
        '!LDbJj7GsQRE*i1U{O??FT|FDC@Rc5&wx0=?s`oZCdBW@QiDNx9K>;!8j)p0l_4a{I><U(e0o&Tqq-8+bx?G+FqLKzgAh`5Q4A?Q'
        '2r&YmkClLLY|Q5iDIw7pVng{t5C{cW3F8oeFMwebvmP;<EL&6<ClbtKuE!Jbc|5)V+XZam5M*h`!629;&5XfF7<|n7AdG{iX$G+w'
        '+_XXS-`@(xWwu<#As~YsU|XrF19H=q_2v#6)8RjL{~z=(ryG>w'
    )),
)

@lru_cache(maxsize=1)
def _decoded():
    # Each 128-byte page is looked up directly: no linear fragment search
    # in the 60 Hz game loop.  These are sparse tables, not a CPU memory image.
    pages = {}
    for lo, hi, encoded in _DATA_BLOCKS:
        blob = memoryview(zlib.decompress(base64.b85decode(encoded)))
        for a in range(lo, hi, 128):
            pages[a >> 7] = blob[a-lo:a-lo+128]
    return pages

class DataTables:
    """Read-only byte-addressable view onto native gameplay data only.

    This is a temporary compatibility layer for historical address lookups.
    It is NOT a full program ROM image, MAME emulation, or a 68010 CPU.
    """
    def __len__(self):
        return 0x88000

    def _byte(self, address):
        if not 0 <= address < 0x88000:
            raise IndexError(f'Game table offset outside data area: {address:#x}')
        if LEVEL2_DYNAMIC_CELL_START <= address < LEVEL2_DYNAMIC_CELL_END:
            return _level2_cell_data()[address - LEVEL2_DYNAMIC_CELL_START]
        if SPRITE_LAYOUT_START <= address < SPRITE_LAYOUT_END:
            return _sprite_layout_bytes()[address - SPRITE_LAYOUT_START]
        if ULTIMATE_OCCLUSION_START <= address < ULTIMATE_OCCLUSION_END:
            return _ultimate_occlusion_blob()[address - ULTIMATE_OCCLUSION_START]
        if BANK1_ZERO_PROFILE_START <= address < BANK1_ZERO_PROFILE_END:
            return 0  # verified original constant-filled profile padding
        page = _decoded().get(address >> 7)
        if page is not None and (address & 127) < len(page):
            return page[address & 127]
        # Atari unpopulated program memory is FF, not a lookup table.
        if 0x30000 <= address < 0x80000:
            return 0xff
        raise LookupError(
            f'ROM-free table not converted at address {address:#07x}. '
            'This branch needs another native data table; '
            'do not substitute a zero or include executable ROM code.'
        )

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return bytes(self._byte(i) for i in range(start, stop, step))
        if index < 0:
            index += len(self)
        return self._byte(index)

    def read16(self, address):
        return (self[address] << 8) | self[address+1]

    def read32(self, address):
        return (self.read16(address) << 16) | self.read16(address+2)

DATA_TABLES = DataTables()
