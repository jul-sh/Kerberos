#!/usr/bin/env python3
"""Inject the Caleuche interpolation axis (CALE 0-100) into a compiled Kerberos variable font.

    CALE 0    Kerberos (the drawn serif/italic design; exact invariance across all masters)
    CALE 100  Iosevka Caleuche (point-compatible outlines, composites, metrics, and YTLC)

All existing Kerberos variation tuples and HVAR/GDEF regions remain untouched at CALE 0 and are
cancelled linearly at CALE 100, while Caleuche's own weight (300, 400, 500, 700), lowercase
height (YTLC 500-550), and small optical-size x-height raise are active along CALE 0..100.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import enum
import json
import math
from pathlib import Path
from typing import Callable
import zlib

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.ttLib.tables._f_v_a_r import Axis
from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates
from fontTools.varLib.builder import buildVarData

ROOT = Path(__file__).resolve().parents[1]
MASTERS_PATH = ROOT / "sources" / "caleuche_masters.json.zlib"

TAG = "CALE"
NAME = "Caleuche"
MIN_VALUE, DEFAULT_VALUE, MAX_VALUE = 0.0, 0.0, 100.0
CALE_TENT: tuple[float, float, float] = (0.0, 1.0, 1.0)
YTLC_TENT: tuple[float, float, float] = (0.0, 1.0, 1.0)
OPSZ_SMALL_TENT: tuple[float, float, float] = (-1.0, -1.0, 0.0)

class CaleucheStyle(enum.Enum):
    ROMAN = ("Kerberos", "Roman", 0.0, 5053 / 16384)
    ITALIC = ("Kerberos-Italic", "Italic", 0.2548968, 5057 / 16384)

    def __init__(
        self, stem: str, dataset_key: str, tan_italic: float, w_500_peak: float
    ) -> None:
        self.stem = stem
        self.dataset_key = dataset_key
        self.tan_italic = tan_italic
        self.w_500_peak = w_500_peak

    @classmethod
    def from_stem(cls, stem: str) -> CaleucheStyle:
        for member in cls:
            if member.stem == stem:
                return member
        raise ValueError(f"Unknown style stem: {stem!r}")


class DeltaWeightStop(enum.Enum):
    LIGHT = "Light"
    MEDIUM = "Medium"
    BOLD = "Bold"

    def __init__(self, key: str) -> None:
        self.key = key

    def tent_for(self, style: CaleucheStyle) -> tuple[float, float, float]:
        match self:
            case DeltaWeightStop.LIGHT:
                return (-1.0, -1.0, 0.0)
            case DeltaWeightStop.MEDIUM:
                return (0.0, style.w_500_peak, 1.0)
            case DeltaWeightStop.BOLD:
                return (style.w_500_peak, 1.0, 1.0)


@dataclass(frozen=True)
class EmptyGlyphMaster:
    advance: int


@dataclass(frozen=True)
class SimpleGlyphMaster:
    regular_coords: list[tuple[int, int]]
    weight_coords: dict[DeltaWeightStop, list[tuple[int, int]]]
    advance: int
    regular_ytlc: list[tuple[int, int]]
    weight_ytlc: dict[DeltaWeightStop, list[tuple[int, int]]]


@dataclass(frozen=True)
class CompositeGlyphMaster:
    regular_offsets: list[tuple[int, int]]
    weight_offsets: dict[DeltaWeightStop, list[tuple[int, int]]]
    advance: int
    regular_ytlc: list[tuple[int, int]]
    weight_ytlc: dict[DeltaWeightStop, list[tuple[int, int]]]


GlyphMaster = EmptyGlyphMaster | SimpleGlyphMaster | CompositeGlyphMaster


@dataclass(frozen=True)
class StyleMasterSet:
    style: CaleucheStyle
    glyphs: dict[str, GlyphMaster]
    sx_heights: dict[str, int]
    cap_heights: dict[str, int]


@dataclass(frozen=True)
class ExistingDeviceRef:
    outer: int
    inner: int


@dataclass(frozen=True)
class NoDeviceRef:
    pass


DeviceRef = ExistingDeviceRef | NoDeviceRef


@dataclass(frozen=True)
class PendingAnchorDevice:
    anchor: otTables.Anchor
    attr: str
    reg_tuple: tuple[int, ...]
    inner_idx: int


@dataclass(frozen=True)
class PendingValueDevice:
    value_record: otTables.ValueRecord
    attr: str
    reg_tuple: tuple[int, ...]
    inner_idx: int


PendingDevice = PendingAnchorDevice | PendingValueDevice


class CaleucheWidthMode(enum.Enum):
    PROPORTIONAL = ("proportional", ROOT / "sources" / "caleuche_masters.json.zlib")
    SERIF_ONLY = ("serif_only", ROOT / "sources" / "caleuche_masters_serifonly.json.zlib")

    def __init__(self, key: str, masters_path: Path) -> None:
        self.key = key
        self.masters_path = masters_path


class TopAnchorShiftPolicy(enum.Enum):
    SHORT_ASCENDER_LIFT = "short_ascender_lift"
    TRACK_TOP_EXTENT = "track_top_extent"
    KEEP_BASE_ANCHOR = "keep_base_anchor"

    @classmethod
    def resolve(
        cls, ky1: int, cy1: int, xh_reg: int, cap_reg: int
    ) -> TopAnchorShiftPolicy:
        if xh_reg + 60 < cy1 < cap_reg - 40:
            return cls.SHORT_ASCENDER_LIFT
        if abs(cy1 - ky1) > 15:
            return cls.TRACK_TOP_EXTENT
        return cls.KEEP_BASE_ANCHOR

    def compute_dy(self, ky1: int, cy1: int) -> int:
        match self:
            case TopAnchorShiftPolicy.SHORT_ASCENDER_LIFT:
                return max(cy1 - ky1, 32)
            case TopAnchorShiftPolicy.TRACK_TOP_EXTENT:
                return cy1 - ky1
            case TopAnchorShiftPolicy.KEEP_BASE_ANCHOR:
                return 0


_DATASET_CACHE: dict[tuple[CaleucheStyle, CaleucheWidthMode], StyleMasterSet] = {}


def _to_pairs(raw_list: list[list[int]]) -> list[tuple[int, int]]:
    return [(int(pair[0]), int(pair[1])) for pair in raw_list]


def load_style_masters(
    style: CaleucheStyle,
    width_mode: CaleucheWidthMode = CaleucheWidthMode.PROPORTIONAL,
) -> StyleMasterSet:
    cache_key = (style, width_mode)
    if cache_key in _DATASET_CACHE:
        return _DATASET_CACHE[cache_key]
    raw_all = json.loads(zlib.decompress(width_mode.masters_path.read_bytes()).decode("utf-8"))
    raw = raw_all[style.dataset_key]
    simple_coords = raw["simple_coords"]
    simple_advs = raw["simple_advs"]["Regular"]
    simple_ytlc = raw["simple_ytlc"]
    comp_offsets = raw["comp_offsets"]
    comp_advs = raw["comp_advs"]["Regular"]
    comp_ytlc = raw["comp_ytlc"]

    glyphs: dict[str, GlyphMaster] = {}
    for gname, reg_pts_raw in simple_coords["Regular"].items():
        adv = int(simple_advs[gname])
        reg_pts = _to_pairs(reg_pts_raw)
        if not reg_pts:
            glyphs[gname] = EmptyGlyphMaster(advance=adv)
        else:
            glyphs[gname] = SimpleGlyphMaster(
                regular_coords=reg_pts,
                weight_coords={
                    stop: _to_pairs(simple_coords[stop.key][gname]) for stop in DeltaWeightStop
                },
                advance=adv,
                regular_ytlc=_to_pairs(simple_ytlc["Regular"][gname]),
                weight_ytlc={
                    stop: _to_pairs(simple_ytlc[stop.key][gname]) for stop in DeltaWeightStop
                },
            )

    for gname, reg_offs_raw in comp_offsets["Regular"].items():
        glyphs[gname] = CompositeGlyphMaster(
            regular_offsets=_to_pairs(reg_offs_raw),
            weight_offsets={
                stop: _to_pairs(comp_offsets[stop.key][gname]) for stop in DeltaWeightStop
            },
            advance=int(comp_advs[gname]),
            regular_ytlc=_to_pairs(comp_ytlc["Regular"][gname]),
            weight_ytlc={
                stop: _to_pairs(comp_ytlc[stop.key][gname]) for stop in DeltaWeightStop
            },
        )

    master_set = StyleMasterSet(
        style=style,
        glyphs=glyphs,
        sx_heights={k: int(v) for k, v in raw["sx_heights"].items()},
        cap_heights={k: int(v) for k, v in raw["cap_heights"].items()},
    )
    _DATASET_CACHE[cache_key] = master_set
    return master_set


def _make_region_axis(start: float, peak: float, end: float) -> otTables.VarRegionAxis:
    axis = otTables.VarRegionAxis()
    axis.StartCoord = start
    axis.PeakCoord = peak
    axis.EndCoord = end
    return axis


def _make_region(coords: list[tuple[float, float, float]]) -> otTables.VarRegion:
    region = otTables.VarRegion()
    region.VarRegionAxis = [_make_region_axis(s, p, e) for s, p, e in coords]
    return region


def _append_caleuche_tuple(
    out: list[TupleVariation],
    axes: dict[str, tuple[float, float, float]],
    delta: list[tuple[int, int]],
) -> None:
    if not any(dx or dy for dx, dy in delta):
        return
    tv = TupleVariation(axes, delta)
    out.append(tv)


def _append_caleuche_tuples(
    out: list[TupleVariation],
    style: CaleucheStyle,
    k_pts: list[tuple[int, int]],
    adv_k: int,
    reg_pts: list[tuple[int, int]],
    weight_pts: dict[DeltaWeightStop, list[tuple[int, int]]],
    adv_c: int,
    reg_ytlc: list[tuple[int, int]],
    weight_ytlc: dict[DeltaWeightStop, list[tuple[int, int]]],
    width_mode: CaleucheWidthMode = CaleucheWidthMode.PROPORTIONAL,
) -> None:
    zero_phantoms = [(0, 0), (0, 0), (0, 0), (0, 0)]
    match width_mode:
        case CaleucheWidthMode.PROPORTIONAL:
            cale_phantoms = [(0, 0), (adv_c - adv_k, 0), (0, 0), (0, 0)]
        case CaleucheWidthMode.SERIF_ONLY:
            cale_phantoms = zero_phantoms

    # 1. Pure CALE delta at Regular (wght=400, opsz=12, GRAD=0, YTLC=500)
    pure_cale = [(cx - kx, cy - ky) for (kx, ky), (cx, cy) in zip(k_pts, reg_pts)] + cale_phantoms
    _append_caleuche_tuple(out, {TAG: CALE_TENT}, pure_cale)

    # 2. Caleuche weight stops (Light=300, Medium=500, Bold=700) relative to Regular=400
    for stop in DeltaWeightStop:
        w_pts = weight_pts[stop]
        w_delta = [(wx - rx, wy - ry) for (rx, ry), (wx, wy) in zip(reg_pts, w_pts)] + zero_phantoms
        _append_caleuche_tuple(out, {"wght": stop.tent_for(style), TAG: CALE_TENT}, w_delta)

    # 3. Caleuche YTLC=550 and small-opsz (opsz=6) raise
    _append_caleuche_tuple(
        out,
        {"YTLC": YTLC_TENT, TAG: CALE_TENT},
        reg_ytlc + zero_phantoms,
    )
    for stop in DeltaWeightStop:
        w_ytlc = weight_ytlc[stop]
        wy_delta = [(wyx - ryx, wyy - ryy) for (ryx, ryy), (wyx, wyy) in zip(reg_ytlc, w_ytlc)] + zero_phantoms
        _append_caleuche_tuple(
            out,
            {"wght": stop.tent_for(style), "YTLC": YTLC_TENT, TAG: CALE_TENT},
            wy_delta,
        )
    half_reg_ytlc = [(int(round(0.5 * dx)), int(round(0.5 * dy))) for dx, dy in reg_ytlc]
    _append_caleuche_tuple(
        out,
        {"opsz": OPSZ_SMALL_TENT, TAG: CALE_TENT},
        half_reg_ytlc + zero_phantoms,
    )
    _append_caleuche_tuple(
        out,
        {"opsz": OPSZ_SMALL_TENT, "YTLC": YTLC_TENT, TAG: CALE_TENT},
        [(-dx, -dy) for dx, dy in half_reg_ytlc] + zero_phantoms,
    )


def _should_negate_kerberos_tuple(
    axes: dict[str, tuple[float, float, float]],
) -> bool:
    return bool(axes)


def _update_gvar(
    font: TTFont,
    masters: StyleMasterSet,
    width_mode: CaleucheWidthMode = CaleucheWidthMode.PROPORTIONAL,
) -> None:
    glyf = font["glyf"]
    metrics = font["hmtx"].metrics
    gvar = font["gvar"].variations
    style = masters.style

    for gname in font.getGlyphOrder():
        base, _ = glyf._getCoordinatesAndControls(gname, metrics)
        adv_k = metrics[gname][0]
        existing = list(gvar.get(gname, []))
        master = masters.glyphs[gname]
        updated = list(existing)

        # Cancel active Kerberos variations linearly along CALE 0 -> 100 by direct negation
        for variation in existing:
            if not _should_negate_kerberos_tuple(variation.axes):
                continue
            negated = [
                (-c[0], -c[1]) if c is not None else None for c in variation.coordinates
            ]
            match width_mode:
                case CaleucheWidthMode.PROPORTIONAL:
                    pass
                case CaleucheWidthMode.SERIF_ONLY:
                    negated[-4:] = [(0, 0), (0, 0), (0, 0), (0, 0)]
            if any(c is not None and (c[0] or c[1]) for c in negated):
                updated.append(TupleVariation({**variation.axes, TAG: CALE_TENT}, negated))

        n_pts = len(base) - 4
        k_pts = [(int(round(x)), int(round(y))) for x, y in base[:n_pts]]

        match master:
            case EmptyGlyphMaster(advance=adv_c):
                match width_mode:
                    case CaleucheWidthMode.PROPORTIONAL:
                        d_adv = adv_c - adv_k
                    case CaleucheWidthMode.SERIF_ONLY:
                        d_adv = 0
                _append_caleuche_tuple(
                    updated,
                    {TAG: CALE_TENT},
                    [(0, 0), (d_adv, 0), (0, 0), (0, 0)],
                )
            case SimpleGlyphMaster(
                regular_coords=reg_coords,
                weight_coords=weight_coords,
                advance=adv_c,
                regular_ytlc=reg_ytlc,
                weight_ytlc=weight_ytlc,
            ):
                _append_caleuche_tuples(
                    updated,
                    style,
                    k_pts,
                    adv_k,
                    reg_coords,
                    weight_coords,
                    adv_c,
                    reg_ytlc,
                    weight_ytlc,
                    width_mode=width_mode,
                )
            case CompositeGlyphMaster(
                regular_offsets=reg_offsets,
                weight_offsets=weight_offsets,
                advance=adv_c,
                regular_ytlc=reg_ytlc,
                weight_ytlc=weight_ytlc,
            ):
                _append_caleuche_tuples(
                    updated,
                    style,
                    k_pts,
                    adv_k,
                    reg_offsets,
                    weight_offsets,
                    adv_c,
                    reg_ytlc,
                    weight_ytlc,
                    width_mode=width_mode,
                )

        gvar[gname] = updated


def _update_hvar(
    font: TTFont,
    masters: StyleMasterSet,
    width_mode: CaleucheWidthMode = CaleucheWidthMode.PROPORTIONAL,
) -> None:
    if "HVAR" not in font:
        return
    vs = font["HVAR"].table.VarStore
    regions = list(vs.VarRegionList.Region)
    orig_count = len(regions)

    for region in regions:
        region.VarRegionAxis.append(_make_region_axis(0.0, 0.0, 0.0))

    match width_mode:
        case CaleucheWidthMode.SERIF_ONLY:
            vs.VarRegionList.Region = regions
            vs.VarRegionList.RegionCount = len(regions)
            vs.VarRegionList.RegionAxisCount = 5
            return
        case CaleucheWidthMode.PROPORTIONAL:
            pass

    for i in range(orig_count):
        base_axes = [(a.StartCoord, a.PeakCoord, a.EndCoord) for a in regions[i].VarRegionAxis[:4]]
        regions.append(_make_region(base_axes + [CALE_TENT]))
    pure_cale_idx = len(regions)
    regions.append(_make_region([(0.0, 0.0, 0.0)] * 4 + [CALE_TENT]))

    vs.VarRegionList.Region = regions
    vs.VarRegionList.RegionCount = len(regions)
    vs.VarRegionList.RegionAxisCount = 5

    metrics = font["hmtx"].metrics
    glyph_order = font.getGlyphOrder()
    mapping = font["HVAR"].table.AdvWidthMap

    # Snapshot original per-region deltas for every (outer, inner) item in HVAR
    vd_snapshots = [
        (list(vd.VarRegionIndex), [list(row) for row in vd.Item]) for vd in vs.VarData
    ]
    new_region_indices = list(range(len(regions)))
    new_items: list[list[int]] = []
    item_index_by_vec: dict[tuple[int, ...], int] = {}
    new_mapping: dict[str, int] = {}

    for gname in glyph_order:
        packed = mapping.mapping[gname]
        outer, inner = packed >> 16, packed & 0xFFFF
        orig_reg_indices, orig_rows = vd_snapshots[outer]
        orig_row = orig_rows[inner]
        full_orig = [0] * orig_count
        for reg_idx, val in zip(orig_reg_indices, orig_row):
            full_orig[reg_idx] = int(val)
        adv_k = metrics[gname][0]
        adv_c = masters.glyphs[gname].advance
        vec = tuple(full_orig + [-v for v in full_orig] + [adv_c - adv_k])
        row_idx = item_index_by_vec.get(vec)
        if row_idx is None:
            row_idx = len(new_items)
            item_index_by_vec[vec] = row_idx
            new_items.append(list(vec))
        new_mapping[gname] = row_idx

    vs.VarData = [buildVarData(new_region_indices, new_items, optimize=True)]
    vs.VarDataCount = 1
    mapping.mapping = new_mapping
    _ = pure_cale_idx


def _update_mvar(font: TTFont, masters: StyleMasterSet) -> None:
    if "MVAR" not in font:
        return
    mvar = font["MVAR"].table
    vs = mvar.VarStore
    regions = list(vs.VarRegionList.Region)
    orig_count = len(regions)
    style = masters.style

    for region in regions:
        region.VarRegionAxis.append(_make_region_axis(0.0, 0.0, 0.0))
    for i in range(orig_count):
        base_axes = [(a.StartCoord, a.PeakCoord, a.EndCoord) for a in regions[i].VarRegionAxis[:4]]
        regions.append(_make_region(base_axes + [CALE_TENT]))

    regions.append(_make_region([(0.0, 0.0, 0.0)] * 4 + [CALE_TENT]))
    for stop in DeltaWeightStop:
        regions.append(
            _make_region(
                [stop.tent_for(style), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), CALE_TENT]
            )
        )
    regions.append(_make_region([(0.0, 0.0, 0.0)] * 3 + [YTLC_TENT, CALE_TENT]))
    regions.append(
        _make_region([(0.0, 0.0, 0.0), OPSZ_SMALL_TENT, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), CALE_TENT])
    )
    regions.append(
        _make_region([(0.0, 0.0, 0.0), OPSZ_SMALL_TENT, (0.0, 0.0, 0.0), YTLC_TENT, CALE_TENT])
    )

    vs.VarRegionList.Region = regions
    vs.VarRegionList.RegionCount = len(regions)
    vs.VarRegionList.RegionAxisCount = 5

    vd_snapshots = [
        (list(vd.VarRegionIndex), [list(row) for row in vd.Item]) for vd in vs.VarData
    ]
    k_xh = int(font["OS/2"].sxHeight)
    k_cap = int(font["OS/2"].sCapHeight)
    c_xh_reg = masters.sx_heights["Regular"]
    c_cap_reg = masters.cap_heights["Regular"]
    d_ytlc_xh = int(round(c_xh_reg * (550.0 / 500.0 - 1.0)))
    half_ytlc_xh = int(round(0.5 * d_ytlc_xh))
    d_ytlc_stro = int(round(0.6 * c_xh_reg * (550.0 / 500.0 - 1.0)))
    half_ytlc_stro = int(round(0.5 * d_ytlc_stro))

    new_items: list[list[int]] = []
    for vr_idx, vr in enumerate(mvar.ValueRecord):
        outer, inner = vr.VarIdx >> 16, vr.VarIdx & 0xFFFF
        orig_regs, orig_rows = vd_snapshots[outer]
        orig_row = orig_rows[inner]
        full_orig = [0] * orig_count
        for reg_idx, val in zip(orig_regs, orig_row):
            full_orig[reg_idx] = int(val)
        match vr.ValueTag:
            case "cpht":
                extra = (
                    [c_cap_reg - k_cap]
                    + [masters.cap_heights[s.key] - c_cap_reg for s in DeltaWeightStop]
                    + [0, 0, 0]
                )
            case "xhgt":
                extra = (
                    [c_xh_reg - k_xh]
                    + [masters.sx_heights[s.key] - c_xh_reg for s in DeltaWeightStop]
                    + [d_ytlc_xh, half_ytlc_xh, -half_ytlc_xh]
                )
            case "stro":
                extra = (
                    [int(round(0.6 * (c_xh_reg - k_xh)))]
                    + [int(round(0.6 * (masters.sx_heights[s.key] - c_xh_reg))) for s in DeltaWeightStop]
                    + [d_ytlc_stro, half_ytlc_stro, -half_ytlc_stro]
                )
            case _:
                extra = [0] * (1 + len(DeltaWeightStop) + 3)
        new_items.append(full_orig + [-v for v in full_orig] + extra)
        vr.VarIdx = vr_idx

    vs.VarData = [buildVarData(list(range(len(regions))), new_items, optimize=True)]
    vs.VarDataCount = 1


def _device_ref(device: otTables.Device | None) -> DeviceRef:
    if device is not None and device.DeltaFormat == 0x8000:
        return ExistingDeviceRef(outer=device.StartSize, inner=device.EndSize)
    return NoDeviceRef()


def _make_var_device(outer: int, inner: int) -> otTables.Device:
    dev = otTables.Device()
    dev.StartSize = outer
    dev.EndSize = inner
    dev.DeltaFormat = 0x8000
    return dev


_KERN_BIN_SIZE = 5


class CaleucheKernAccentMode(enum.Enum):
    ALL_MARKS = "all_marks"
    NO_TOP_MARK = "no_top_mark"
    TOP_MARK_ONLY = "top_mark_only"


class CurvePointKind(enum.Enum):
    ON_CURVE = "on_curve"
    OFF_CURVE = "off_curve"

    @classmethod
    def from_flag(cls, flag: int) -> CurvePointKind:
        return cls.ON_CURVE if (flag & 1) else cls.OFF_CURVE


class CompositeMarkPlacement(enum.Enum):
    HAS_TOP_MARK = "has_top_mark"
    NO_TOP_MARK = "no_top_mark"

    @classmethod
    def classify(
        cls, glyf, masters: StyleMasterSet, gname: str
    ) -> CompositeMarkPlacement:
        master = masters.glyphs.get(gname)
        if not isinstance(master, CompositeGlyphMaster):
            return cls.NO_TOP_MARK
        comps = glyf[gname].components
        for idx, comp in enumerate(comps[1:], start=1):
            cm = masters.glyphs.get(comp.glyphName)
            if isinstance(cm, SimpleGlyphMaster) and cm.regular_coords:
                oy = master.regular_offsets[idx][1]
                if max(y + oy for _, y in cm.regular_coords) > 485:
                    return cls.HAS_TOP_MARK
        return cls.NO_TOP_MARK


class CaleucheLeftKernClass(enum.Enum):
    UC_W = ("W", ("W",), ())
    UC_V = ("V", ("V",), ())
    UC_T = ("T", ("T",), ())
    UC_Y = ("Y", ("Y",), ())
    UC_F = ("F", ("F",), ())
    UC_P = ("P", ("P",), ())
    UC_K = ("K", ("K",), ())
    UC_R = ("R", ("R",), ())
    UC_L = ("L", ("L",), ())
    UC_A = ("A", ("A",), ())
    UC_ROUND = ("O", ("O", "D", "Q"), ())
    LC_F = ("f", ("f",), ("f_f",))
    LC_R = ("r", ("r",), ())
    LC_VY = ("v", ("v", "y"), ())
    LC_W = ("w", ("w",), ())
    LC_K = ("k", ("k",), ("f_k", "f_f_k"))
    LC_T = ("t", ("t",), ("t_t", "f_t", "f_f_t"))
    LC_ROUND = ("o", ("o", "e", "c"), ("ae", "oe"))
    LC_A = ("a", ("a",), ())

    def __init__(
        self,
        rep_glyph: str,
        base_glyphs: tuple[str, ...],
        extra_glyphs: tuple[str, ...],
    ) -> None:
        self.rep_glyph = rep_glyph
        self.base_glyphs = base_glyphs
        self.extra_glyphs = extra_glyphs


class CaleucheRightKernClass(enum.Enum):
    UC_A = ("A", ("A",), ("AE",), CaleucheKernAccentMode.ALL_MARKS)
    UC_V = ("V", ("V",), (), CaleucheKernAccentMode.ALL_MARKS)
    UC_W = ("W", ("W",), (), CaleucheKernAccentMode.ALL_MARKS)
    UC_Y = ("Y", ("Y",), (), CaleucheKernAccentMode.ALL_MARKS)
    UC_T = ("T", ("T",), (), CaleucheKernAccentMode.ALL_MARKS)
    UC_ROUND = ("O", ("O", "C", "G", "Q"), ("OE",), CaleucheKernAccentMode.ALL_MARKS)
    UC_LOW_LEFT_J = (
        "J",
        ("J",),
        ("uni0408", "Je-cy"),
        CaleucheKernAccentMode.ALL_MARKS,
    )
    LC_ROUND = (
        "o",
        ("o", "e", "c", "g", "q", "s"),
        ("oe",),
        CaleucheKernAccentMode.NO_TOP_MARK,
    )
    LC_D = ("d", ("d",), (), CaleucheKernAccentMode.NO_TOP_MARK)
    LC_ROUND_ACCENT = (
        "oacute",
        ("o", "e", "c", "g", "q", "s"),
        (),
        CaleucheKernAccentMode.TOP_MARK_ONLY,
    )
    LC_A = ("a", ("a",), ("ae",), CaleucheKernAccentMode.NO_TOP_MARK)
    LC_A_ACCENT = ("aacute", ("a",), (), CaleucheKernAccentMode.TOP_MARK_ONLY)
    LC_U = ("u", ("u",), (), CaleucheKernAccentMode.NO_TOP_MARK)
    LC_U_ACCENT = ("uacute", ("u",), (), CaleucheKernAccentMode.TOP_MARK_ONLY)
    LC_R = ("r", ("r",), (), CaleucheKernAccentMode.NO_TOP_MARK)
    LC_R_ACCENT = ("racute", ("r",), (), CaleucheKernAccentMode.TOP_MARK_ONLY)
    LC_VY = ("v", ("v", "y"), (), CaleucheKernAccentMode.NO_TOP_MARK)
    LC_VY_ACCENT = ("yacute", ("v", "y"), (), CaleucheKernAccentMode.TOP_MARK_ONLY)
    LC_W = ("w", ("w",), (), CaleucheKernAccentMode.NO_TOP_MARK)
    LC_W_ACCENT = ("wacute", ("w",), (), CaleucheKernAccentMode.TOP_MARK_ONLY)
    LC_X = ("x", ("x",), (), CaleucheKernAccentMode.NO_TOP_MARK)
    LC_T = ("t", ("t",), ("t_t",), CaleucheKernAccentMode.ALL_MARKS)
    PUNCT_LOW = (
        "period",
        ("period", "comma"),
        ("ellipsis", "quotesinglbase", "quotedblbase"),
        CaleucheKernAccentMode.NO_TOP_MARK,
    )

    def __init__(
        self,
        rep_glyph: str,
        base_glyphs: tuple[str, ...],
        extra_glyphs: tuple[str, ...],
        accent_mode: CaleucheKernAccentMode,
    ) -> None:
        self.rep_glyph = rep_glyph
        self.base_glyphs = base_glyphs
        self.extra_glyphs = extra_glyphs
        self.accent_mode = accent_mode


@dataclass(frozen=True)
class PairOpticalPolicy:
    y_band: tuple[float, float]
    target_gap: float
    avg_weight: float
    gain: float
    min_clearance: float
    max_tighten: int


class PairInteractionKind(enum.Enum):
    NONE = PairOpticalPolicy((0.0, 0.0), 0.0, 0.0, 0.0, 36.0, 0)
    CAP_OVERHANG_LC = PairOpticalPolicy(
        y_band=(20.0, 445.0),
        target_gap=152.0,
        avg_weight=0.30,
        gain=0.65,
        min_clearance=50.0,
        max_tighten=-64,
    )
    CAP_DIAG_LC = PairOpticalPolicy(
        y_band=(20.0, 445.0),
        target_gap=104.0,
        avg_weight=0.32,
        gain=0.72,
        min_clearance=50.0,
        max_tighten=-62,
    )
    CAP_CLEFT_LC = PairOpticalPolicy(
        y_band=(135.0, 425.0),
        target_gap=104.0,
        avg_weight=0.38,
        gain=0.68,
        min_clearance=50.0,
        max_tighten=-42,
    )
    CAP_LOWFOOT_LC = PairOpticalPolicy(
        y_band=(15.0, 450.0),
        target_gap=162.0,
        avg_weight=0.25,
        gain=0.60,
        min_clearance=54.0,
        max_tighten=-34,
    )
    CAP_OVERHANG_J = PairOpticalPolicy(
        y_band=(20.0, 195.0),
        target_gap=118.0,
        avg_weight=0.30,
        gain=0.65,
        min_clearance=52.0,
        max_tighten=-56,
    )
    CAP_A_LC = PairOpticalPolicy(
        y_band=(120.0, 450.0),
        target_gap=102.0,
        avg_weight=0.32,
        gain=0.70,
        min_clearance=48.0,
        max_tighten=-45,
    )
    CAP_CAP_DIAG = PairOpticalPolicy(
        y_band=(40.0, 690.0),
        target_gap=96.0,
        avg_weight=0.30,
        gain=0.72,
        min_clearance=52.0,
        max_tighten=-76,
    )
    CAP_CAP_ROUND = PairOpticalPolicy(
        y_band=(60.0, 670.0),
        target_gap=92.0,
        avg_weight=0.38,
        gain=0.70,
        min_clearance=48.0,
        max_tighten=-45,
    )
    LC_F_LC = PairOpticalPolicy(
        y_band=(20.0, 445.0),
        target_gap=88.0,
        avg_weight=0.34,
        gain=0.74,
        min_clearance=48.0,
        max_tighten=-50,
    )
    LC_RT_LC = PairOpticalPolicy(
        y_band=(20.0, 350.0),
        target_gap=94.0,
        avg_weight=0.32,
        gain=0.55,
        min_clearance=36.0,
        max_tighten=-22,
    )
    LC_DIAG_ROUND = PairOpticalPolicy(
        y_band=(25.0, 440.0),
        target_gap=82.0,
        avg_weight=0.30,
        gain=0.70,
        min_clearance=44.0,
        max_tighten=-36,
    )
    CAP_OVERHANG_PUNCT = PairOpticalPolicy(
        y_band=(0.0, 145.0),
        target_gap=165.0,
        avg_weight=0.25,
        gain=0.60,
        min_clearance=55.0,
        max_tighten=-72,
    )
    LC_OVERHANG_PUNCT = PairOpticalPolicy(
        y_band=(0.0, 145.0),
        target_gap=165.0,
        avg_weight=0.25,
        gain=0.45,
        min_clearance=55.0,
        max_tighten=-45,
    )

    def __init__(self, policy: PairOpticalPolicy) -> None:
        self.policy = policy

    @classmethod
    def resolve(
        cls, left: CaleucheLeftKernClass, right: CaleucheRightKernClass
    ) -> PairInteractionKind:
        lc_overhang_targets = (
            CaleucheRightKernClass.LC_ROUND,
            CaleucheRightKernClass.LC_D,
            CaleucheRightKernClass.LC_ROUND_ACCENT,
            CaleucheRightKernClass.LC_A,
            CaleucheRightKernClass.LC_A_ACCENT,
            CaleucheRightKernClass.LC_U,
            CaleucheRightKernClass.LC_U_ACCENT,
            CaleucheRightKernClass.LC_R,
            CaleucheRightKernClass.LC_R_ACCENT,
            CaleucheRightKernClass.LC_VY,
            CaleucheRightKernClass.LC_VY_ACCENT,
            CaleucheRightKernClass.LC_W,
            CaleucheRightKernClass.LC_W_ACCENT,
        )
        lc_round_a_targets = (
            CaleucheRightKernClass.LC_ROUND,
            CaleucheRightKernClass.LC_D,
            CaleucheRightKernClass.LC_ROUND_ACCENT,
            CaleucheRightKernClass.LC_A,
            CaleucheRightKernClass.LC_A_ACCENT,
            CaleucheRightKernClass.LC_U,
            CaleucheRightKernClass.LC_U_ACCENT,
        )
        if right is CaleucheRightKernClass.PUNCT_LOW:
            if left in (
                CaleucheLeftKernClass.UC_T,
                CaleucheLeftKernClass.UC_V,
                CaleucheLeftKernClass.UC_W,
                CaleucheLeftKernClass.UC_Y,
                CaleucheLeftKernClass.UC_F,
                CaleucheLeftKernClass.UC_P,
            ):
                return cls.CAP_OVERHANG_PUNCT
            if left in (
                CaleucheLeftKernClass.LC_F,
                CaleucheLeftKernClass.LC_R,
                CaleucheLeftKernClass.LC_VY,
                CaleucheLeftKernClass.LC_W,
            ):
                return cls.LC_OVERHANG_PUNCT

        match left:
            case CaleucheLeftKernClass.UC_T | CaleucheLeftKernClass.UC_F:
                if right in lc_overhang_targets:
                    return cls.CAP_OVERHANG_LC
                if right is CaleucheRightKernClass.UC_LOW_LEFT_J:
                    return cls.CAP_OVERHANG_J
                if right is CaleucheRightKernClass.UC_A:
                    return cls.CAP_CAP_DIAG
                if right is CaleucheRightKernClass.UC_ROUND:
                    return cls.CAP_CAP_ROUND
            case (
                CaleucheLeftKernClass.UC_W
                | CaleucheLeftKernClass.UC_V
                | CaleucheLeftKernClass.UC_Y
            ):
                if right in lc_overhang_targets:
                    return cls.CAP_DIAG_LC
                if right is CaleucheRightKernClass.UC_LOW_LEFT_J:
                    return cls.CAP_OVERHANG_J
                if right is CaleucheRightKernClass.UC_A:
                    return cls.CAP_CAP_DIAG
                if right is CaleucheRightKernClass.UC_ROUND:
                    return cls.CAP_CAP_ROUND
            case (
                CaleucheLeftKernClass.UC_P
                | CaleucheLeftKernClass.UC_K
                | CaleucheLeftKernClass.UC_R
            ):
                if right in lc_overhang_targets:
                    return cls.CAP_CLEFT_LC
                if (
                    left is CaleucheLeftKernClass.UC_P
                    and right is CaleucheRightKernClass.UC_LOW_LEFT_J
                ):
                    return cls.CAP_OVERHANG_J
                if right is CaleucheRightKernClass.UC_A:
                    return cls.CAP_CAP_DIAG
                if right in (
                    CaleucheRightKernClass.UC_ROUND,
                    CaleucheRightKernClass.UC_V,
                    CaleucheRightKernClass.UC_W,
                    CaleucheRightKernClass.UC_Y,
                    CaleucheRightKernClass.UC_T,
                ):
                    return cls.CAP_CAP_ROUND
            case CaleucheLeftKernClass.UC_L:
                if right in (
                    CaleucheRightKernClass.LC_VY,
                    CaleucheRightKernClass.LC_VY_ACCENT,
                    CaleucheRightKernClass.LC_W,
                    CaleucheRightKernClass.LC_W_ACCENT,
                    CaleucheRightKernClass.LC_T,
                ):
                    return cls.CAP_LOWFOOT_LC
                if right in (
                    CaleucheRightKernClass.UC_A,
                    CaleucheRightKernClass.UC_V,
                    CaleucheRightKernClass.UC_W,
                    CaleucheRightKernClass.UC_Y,
                    CaleucheRightKernClass.UC_T,
                ):
                    return cls.CAP_CAP_DIAG
                if right is CaleucheRightKernClass.UC_ROUND:
                    return cls.CAP_CAP_ROUND
            case CaleucheLeftKernClass.UC_A:
                if right in (
                    CaleucheRightKernClass.LC_VY,
                    CaleucheRightKernClass.LC_VY_ACCENT,
                    CaleucheRightKernClass.LC_W,
                    CaleucheRightKernClass.LC_W_ACCENT,
                    CaleucheRightKernClass.LC_T,
                    CaleucheRightKernClass.LC_ROUND,
                    CaleucheRightKernClass.LC_D,
                    CaleucheRightKernClass.LC_ROUND_ACCENT,
                ):
                    return cls.CAP_A_LC
                if right in (
                    CaleucheRightKernClass.UC_V,
                    CaleucheRightKernClass.UC_W,
                    CaleucheRightKernClass.UC_Y,
                    CaleucheRightKernClass.UC_T,
                ):
                    return cls.CAP_CAP_DIAG
                if right is CaleucheRightKernClass.UC_ROUND:
                    return cls.CAP_CAP_ROUND
            case CaleucheLeftKernClass.UC_ROUND:
                if right in (
                    CaleucheRightKernClass.UC_A,
                    CaleucheRightKernClass.UC_V,
                    CaleucheRightKernClass.UC_W,
                    CaleucheRightKernClass.UC_Y,
                    CaleucheRightKernClass.UC_T,
                ):
                    return cls.CAP_CAP_ROUND
            case CaleucheLeftKernClass.LC_F:
                if right in lc_overhang_targets:
                    return cls.LC_F_LC
            case CaleucheLeftKernClass.LC_R | CaleucheLeftKernClass.LC_T:
                if right in lc_round_a_targets:
                    return cls.LC_RT_LC
            case (
                CaleucheLeftKernClass.LC_VY
                | CaleucheLeftKernClass.LC_W
                | CaleucheLeftKernClass.LC_K
            ):
                if right in lc_round_a_targets:
                    return cls.LC_DIAG_ROUND
            case CaleucheLeftKernClass.LC_ROUND | CaleucheLeftKernClass.LC_A:
                if right in (
                    CaleucheRightKernClass.LC_VY,
                    CaleucheRightKernClass.LC_VY_ACCENT,
                    CaleucheRightKernClass.LC_W,
                    CaleucheRightKernClass.LC_W_ACCENT,
                    CaleucheRightKernClass.LC_X,
                    CaleucheRightKernClass.LC_T,
                ):
                    return cls.LC_DIAG_ROUND
        return cls.NONE


def _flatten_quadratic_contour(
    pts: list[tuple[int, int]], flags: list[int]
) -> list[tuple[float, float]]:
    n = len(pts)
    if n == 0:
        return []
    typed_pts = [
        (float(x), float(y), CurvePointKind.from_flag(f)) for (x, y), f in zip(pts, flags)
    ]
    expanded: list[tuple[float, float, CurvePointKind]] = []
    for i in range(n):
        curr = typed_pts[i]
        nxt = typed_pts[(i + 1) % n]
        expanded.append(curr)
        if curr[2] is CurvePointKind.OFF_CURVE and nxt[2] is CurvePointKind.OFF_CURVE:
            expanded.append(
                (
                    0.5 * (curr[0] + nxt[0]),
                    0.5 * (curr[1] + nxt[1]),
                    CurvePointKind.ON_CURVE,
                )
            )
    if expanded[0][2] is CurvePointKind.OFF_CURVE:
        expanded = [expanded[-1]] + expanded[:-1]

    m = len(expanded)
    segments: list[tuple[float, float]] = []
    i = 0
    while i < m:
        p0 = (expanded[i][0], expanded[i][1])
        nxt = expanded[(i + 1) % m]
        match nxt[2]:
            case CurvePointKind.ON_CURVE:
                p1 = (nxt[0], nxt[1])
                dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
                steps = max(1, int(math.ceil(dist / 6.0)))
                for s in range(steps):
                    t = s / steps
                    segments.append(
                        (p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]))
                    )
                i += 1
            case CurvePointKind.OFF_CURVE:
                ctrl = (nxt[0], nxt[1])
                end_pt = expanded[(i + 2) % m]
                p2 = (end_pt[0], end_pt[1])
                dist = math.hypot(ctrl[0] - p0[0], ctrl[1] - p0[1]) + math.hypot(
                    p2[0] - ctrl[0], p2[1] - ctrl[1]
                )
                steps = max(2, int(math.ceil(dist / 6.0)))
                for s in range(steps):
                    t = s / steps
                    omt = 1.0 - t
                    segments.append(
                        (
                            omt * omt * p0[0] + 2.0 * omt * t * ctrl[0] + t * t * p2[0],
                            omt * omt * p0[1] + 2.0 * omt * t * ctrl[1] + t * t * p2[1],
                        )
                    )
                i += 2
    return segments


def _sample_simple_glyph(
    glyf, gname: str, coords: list[tuple[int, int]]
) -> list[tuple[float, float]]:
    g = glyf[gname]
    end_pts = list(g.endPtsOfContours)
    flags = list(g.flags)
    out: list[tuple[float, float]] = []
    start = 0
    for ep in end_pts:
        out.extend(_flatten_quadratic_contour(coords[start : ep + 1], flags[start : ep + 1]))
        start = ep + 1
    return out


def _build_glyph_envelopes(
    glyf,
    masters: StyleMasterSet,
    gname: str,
    simple_cache: dict[tuple[str, int], list[tuple[float, float]]],
) -> tuple[int, list[dict[int, tuple[float, float]]], dict[int, tuple[float, float]]]:
    master = masters.glyphs[gname]
    tan = masters.style.tan_italic

    def _get_simple_state_pts(sname: str, state_idx: int) -> list[tuple[float, float]]:
        key = (sname, state_idx)
        if key in simple_cache:
            return simple_cache[key]
        sm = masters.glyphs.get(sname)
        if not isinstance(sm, SimpleGlyphMaster) or not sm.regular_coords:
            for k in range(8):
                simple_cache[(sname, k)] = []
            return []
        states = [
            sm.regular_coords,
            [(x + dx, y + dy) for (x, y), (dx, dy) in zip(sm.regular_coords, sm.regular_ytlc)],
        ]
        for stop in DeltaWeightStop:
            wc = sm.weight_coords[stop]
            wy = sm.weight_ytlc[stop]
            states.append(wc)
            states.append([(x + dx, y + dy) for (x, y), (dx, dy) in zip(wc, wy)])
        for k, st_coords in enumerate(states):
            simple_cache[(sname, k)] = _sample_simple_glyph(glyf, sname, st_coords)
        return simple_cache[key]

    envelopes_x: list[dict[int, tuple[float, float]]] = []
    reg_u_env: dict[int, tuple[float, float]] = {}

    match master:
        case SimpleGlyphMaster(advance=adv):
            for s_idx in range(8):
                env_x: dict[int, tuple[float, float]] = {}
                for x, y in _get_simple_state_pts(gname, s_idx):
                    b = int(math.floor(y / _KERN_BIN_SIZE))
                    if b in env_x:
                        mn, mx = env_x[b]
                        env_x[b] = (min(mn, x), max(mx, x))
                    else:
                        env_x[b] = (x, x)
                    if s_idx == 0:
                        u = x - y * tan
                        if b in reg_u_env:
                            umn, umx = reg_u_env[b]
                            reg_u_env[b] = (min(umn, u), max(umx, u))
                        else:
                            reg_u_env[b] = (u, u)
                envelopes_x.append(env_x)
            return adv, envelopes_x, reg_u_env
        case CompositeGlyphMaster(
            regular_offsets=reg_offs,
            weight_offsets=w_offs,
            advance=adv,
            regular_ytlc=reg_ytlc,
            weight_ytlc=w_ytlc,
        ):
            comps = glyf[gname].components
            off_states = [
                reg_offs,
                [(x + dx, y + dy) for (x, y), (dx, dy) in zip(reg_offs, reg_ytlc)],
            ]
            for stop in DeltaWeightStop:
                wo = w_offs[stop]
                wy = w_ytlc[stop]
                off_states.append(wo)
                off_states.append([(x + dx, y + dy) for (x, y), (dx, dy) in zip(wo, wy)])
            for s_idx, offs in enumerate(off_states):
                env_x = {}
                for c_idx, comp in enumerate(comps):
                    ox, oy = offs[c_idx]
                    for px, py in _get_simple_state_pts(comp.glyphName, s_idx):
                        x, y = px + ox, py + oy
                        b = int(math.floor(y / _KERN_BIN_SIZE))
                        if b in env_x:
                            mn, mx = env_x[b]
                            env_x[b] = (min(mn, x), max(mx, x))
                        else:
                            env_x[b] = (x, x)
                        if s_idx == 0:
                            u = x - y * tan
                            if b in reg_u_env:
                                umn, umx = reg_u_env[b]
                                reg_u_env[b] = (min(umn, u), max(umx, u))
                            else:
                                reg_u_env[b] = (u, u)
                envelopes_x.append(env_x)
            return adv, envelopes_x, reg_u_env
        case EmptyGlyphMaster(advance=adv):
            return adv, [{} for _ in range(8)], {}


def _expand_kern_class_members(
    glyph_order: list[str],
    glyf,
    masters: StyleMasterSet,
    base_glyphs: tuple[str, ...],
    extra_glyphs: tuple[str, ...],
    accent_mode: CaleucheKernAccentMode,
) -> list[str]:
    base_set = set(base_glyphs)
    members: list[str] = []
    match accent_mode:
        case CaleucheKernAccentMode.ALL_MARKS | CaleucheKernAccentMode.NO_TOP_MARK:
            for g in base_glyphs + extra_glyphs:
                if g in masters.glyphs:
                    members.append(g)
        case CaleucheKernAccentMode.TOP_MARK_ONLY:
            pass

    for gname in glyph_order:
        if gname in members or gname == "Ldot" or gname.endswith(".sc"):
            continue
        if not glyf[gname].isComposite() or not glyf[gname].components:
            continue
        comp0 = glyf[gname].components[0].glyphName
        if comp0 not in base_set:
            continue
        if masters.glyphs[gname].advance != masters.glyphs[comp0].advance:
            continue
        placement = CompositeMarkPlacement.classify(glyf, masters, gname)
        match (accent_mode, placement):
            case (CaleucheKernAccentMode.ALL_MARKS, _):
                members.append(gname)
            case (
                CaleucheKernAccentMode.NO_TOP_MARK,
                CompositeMarkPlacement.NO_TOP_MARK,
            ):
                members.append(gname)
            case (
                CaleucheKernAccentMode.TOP_MARK_ONLY,
                CompositeMarkPlacement.HAS_TOP_MARK,
            ):
                members.append(gname)
            case _:
                pass
    return members


def _solve_class_pair_kern(
    adv_l: int,
    ex_l: list[dict[int, tuple[float, float]]],
    u_l: dict[int, tuple[float, float]],
    ex_r: list[dict[int, tuple[float, float]]],
    u_r: dict[int, tuple[float, float]],
    kind: PairInteractionKind,
) -> int:
    if kind is PairInteractionKind.NONE:
        return 0
    pol = kind.policy
    b0 = int(math.floor(pol.y_band[0] / _KERN_BIN_SIZE))
    b1 = int(math.ceil(pol.y_band[1] / _KERN_BIN_SIZE))
    gaps = [
        (u_r[b][0] + adv_l) - u_l[b][1]
        for b in range(b0, b1 + 1)
        if b in u_l and b in u_r
    ]
    if not gaps:
        return 0
    min_g = min(gaps)
    avg_g = sum(gaps) / len(gaps)
    eff_g = (1.0 - pol.avg_weight) * min_g + pol.avg_weight * avg_g
    raw_kern = int(round(pol.gain * (pol.target_gap - eff_g)))
    if raw_kern >= -7:
        return 0

    base_clearance = max(36.0, pol.min_clearance)
    kern_floor = float(pol.max_tighten)
    for s_idx, (env_l, env_r) in enumerate(zip(ex_l, ex_r)):
        clearance_dist = base_clearance if s_idx in (0, 1) else max(28.0, base_clearance - 8.0)
        b_rad = int(math.ceil(clearance_dist / _KERN_BIN_SIZE)) + 1
        d_sq = clearance_dist * clearance_dist
        for b_l, (_, x_l_max) in env_l.items():
            y_l = (b_l + 0.5) * _KERN_BIN_SIZE
            for b_r in range(b_l - b_rad, b_l + b_rad + 1):
                if b_r not in env_r:
                    continue
                y_r = (b_r + 0.5) * _KERN_BIN_SIZE
                dy = max(0.0, abs(y_r - y_l) - _KERN_BIN_SIZE)
                if dy >= clearance_dist:
                    continue
                req_dx = math.sqrt(max(0.0, d_sq - dy * dy))
                x_r_min, _ = env_r[b_r]
                req_kern = (x_l_max - x_r_min - adv_l) + req_dx
                if req_kern > kern_floor:
                    kern_floor = req_kern

    safe_kern = max(raw_kern, int(math.ceil(kern_floor)))
    return safe_kern if safe_kern <= -8 else 0


def _inject_caleuche_optical_kerning(
    font: TTFont,
    masters: StyleMasterSet,
    r_cale: int,
    register_row: Callable[[DeviceRef, list[int], tuple[int, ...]], tuple[tuple[int, ...], int] | None],
    pending_devices: list[PendingDevice],
) -> None:
    glyf = font["glyf"]
    glyph_order = font.getGlyphOrder()
    simple_cache: dict[tuple[str, int], list[tuple[float, float]]] = {}
    env_cache: dict[
        str, tuple[int, list[dict[int, tuple[float, float]]], dict[int, tuple[float, float]]]
    ] = {}

    def get_env(gname: str):
        if gname not in env_cache:
            env_cache[gname] = _build_glyph_envelopes(glyf, masters, gname, simple_cache)
        return env_cache[gname]

    class1_defs: dict[str, int] = {}
    left_classes = list(CaleucheLeftKernClass)
    for c1_idx, left_cls in enumerate(left_classes, start=1):
        members = _expand_kern_class_members(
            glyph_order,
            glyf,
            masters,
            left_cls.base_glyphs,
            left_cls.extra_glyphs,
            CaleucheKernAccentMode.ALL_MARKS,
        )
        for gname in members:
            class1_defs[gname] = c1_idx

    class2_defs: dict[str, int] = {}
    right_classes = list(CaleucheRightKernClass)
    right_envelopes: dict[CaleucheRightKernClass, list[dict[int, tuple[float, float]]]] = {}
    for c2_idx, right_cls in enumerate(right_classes, start=1):
        members = _expand_kern_class_members(
            glyph_order,
            glyf,
            masters,
            right_cls.base_glyphs,
            right_cls.extra_glyphs,
            right_cls.accent_mode,
        )
        for gname in members:
            class2_defs[gname] = c2_idx
        if right_cls.accent_mode is CaleucheKernAccentMode.TOP_MARK_ONLY:
            # Union envelope across key accented variants (acute, grave, dieresis, circumflex, tilde)
            probe_names = [
                g
                for g in members
                if any(
                    k in g
                    for k in ("acute", "grave", "dieresis", "circumflex", "tilde", "ring", "caron")
                )
            ] or [right_cls.rep_glyph]
            merged_ex: list[dict[int, tuple[float, float]]] = [{} for _ in range(8)]
            for pname in probe_names:
                _, p_ex, _ = get_env(pname)
                for s_i in range(8):
                    for b, (mn, mx) in p_ex[s_i].items():
                        if b in merged_ex[s_i]:
                            cur_mn, cur_mx = merged_ex[s_i][b]
                            merged_ex[s_i][b] = (min(cur_mn, mn), max(cur_mx, mx))
                        else:
                            merged_ex[s_i][b] = (mn, mx)
            right_envelopes[right_cls] = merged_ex
        else:
            right_envelopes[right_cls] = get_env(right_cls.rep_glyph)[1]

    sub = otTables.PairPos()
    sub.Format = 2
    sub.ValueFormat1 = 0x0044
    sub.ValueFormat2 = 0
    cov = otTables.Coverage()
    cov.glyphs = sorted(class1_defs.keys(), key=font.getGlyphID)
    sub.Coverage = cov

    cd1 = otTables.ClassDef()
    cd1.classDefs = class1_defs
    sub.ClassDef1 = cd1

    cd2 = otTables.ClassDef()
    cd2.classDefs = class2_defs
    sub.ClassDef2 = cd2

    n_c1 = len(left_classes) + 1
    n_c2 = len(right_classes) + 1
    sub.Class1Count = n_c1
    sub.Class2Count = n_c2

    class1_records: list[otTables.Class1Record] = []
    for c1_idx in range(n_c1):
        c1_rec = otTables.Class1Record()
        c2_records: list[otTables.Class2Record] = []
        for c2_idx in range(n_c2):
            c2_rec = otTables.Class2Record()
            val = otTables.ValueRecord()
            val.XAdvance = 0
            val.XAdvDevice = None
            if c1_idx > 0 and c2_idx > 0:
                left_cls = left_classes[c1_idx - 1]
                right_cls = right_classes[c2_idx - 1]
                interaction = PairInteractionKind.resolve(left_cls, right_cls)
                if interaction is not PairInteractionKind.NONE:
                    adv_l, ex_l, u_l = get_env(left_cls.rep_glyph)
                    _, _, u_r = get_env(right_cls.rep_glyph)
                    ex_r = right_envelopes[right_cls]
                    kern_cale = _solve_class_pair_kern(
                        adv_l, ex_l, u_l, ex_r, u_r, interaction
                    )
                    if kern_cale != 0:
                        registered = register_row(NoDeviceRef(), [r_cale], (kern_cale,))
                        if registered is not None:
                            pending_devices.append(
                                PendingValueDevice(
                                    val, "XAdvDevice", registered[0], registered[1]
                                )
                            )
            c2_rec.Value1 = val
            c2_rec.Value2 = None
            c2_records.append(c2_rec)
        c1_rec.Class2Record = c2_records
        class1_records.append(c1_rec)
    sub.Class1Record = class1_records

    gpos = font["GPOS"].table
    new_lkp_idx = len(gpos.LookupList.Lookup)
    lkp = otTables.Lookup()
    lkp.LookupType = 2
    lkp.LookupFlag = 8
    lkp.SubTable = [sub]
    lkp.SubTableCount = 1
    gpos.LookupList.Lookup.append(lkp)
    gpos.LookupList.LookupCount = len(gpos.LookupList.Lookup)

    for frec in gpos.FeatureList.FeatureRecord:
        if frec.FeatureTag == "kern":
            frec.Feature.LookupListIndex.append(new_lkp_idx)
            frec.Feature.LookupCount = len(frec.Feature.LookupListIndex)


def _update_gdef_and_gpos(
    font: TTFont,
    masters: StyleMasterSet,
    width_mode: CaleucheWidthMode = CaleucheWidthMode.PROPORTIONAL,
) -> None:
    if "GDEF" not in font or font["GDEF"].table.VarStore is None:
        return
    vs = font["GDEF"].table.VarStore
    regions = list(vs.VarRegionList.Region)
    orig_count = len(regions)

    for region in regions:
        region.VarRegionAxis.append(_make_region_axis(0.0, 0.0, 0.0))
    for i in range(orig_count):
        base_axes = [(a.StartCoord, a.PeakCoord, a.EndCoord) for a in regions[i].VarRegionAxis[:4]]
        regions.append(_make_region(base_axes + [CALE_TENT]))

    r_cale = len(regions)
    regions.append(_make_region([(0.0, 0.0, 0.0)] * 4 + [CALE_TENT]))
    r_ytlc_cale = len(regions)
    regions.append(_make_region([(0.0, 0.0, 0.0)] * 3 + [YTLC_TENT, CALE_TENT]))
    r_opsz_cale = len(regions)
    regions.append(
        _make_region([(0.0, 0.0, 0.0), OPSZ_SMALL_TENT, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), CALE_TENT])
    )
    r_opsz_ytlc_cale = len(regions)
    regions.append(
        _make_region([(0.0, 0.0, 0.0), OPSZ_SMALL_TENT, (0.0, 0.0, 0.0), YTLC_TENT, CALE_TENT])
    )
    cale_region_indices = [r_cale, r_ytlc_cale, r_opsz_cale, r_opsz_ytlc_cale]

    vs.VarRegionList.Region = regions
    vs.VarRegionList.RegionCount = len(regions)
    vs.VarRegionList.RegionAxisCount = 5

    # Snapshot original VarData before adding cancellation columns
    orig_snapshots = [
        (list(vd.VarRegionIndex), [list(row) for row in vd.Item]) for vd in vs.VarData
    ]
    new_var_data = [
        buildVarData(
            orig_regs + [r + orig_count for r in orig_regs],
            [row + [-v for v in row] for row in orig_rows],
            optimize=True,
        )
        for orig_regs, orig_rows in orig_snapshots
    ]

    if "GPOS" not in font:
        vs.VarData = new_var_data
        vs.VarDataCount = len(new_var_data)
        return

    glyf = font["glyf"]
    metrics = font["hmtx"].metrics
    tan_italic = masters.style.tan_italic
    c_xh_reg = masters.sx_heights["Regular"]
    c_cap_reg = masters.cap_heights["Regular"]

    # Precompute base anchor Caleuche deltas (dx_cale, dy_cale, dx_ytlc, dy_ytlc) for simple glyphs
    base_top_shifts: dict[str, tuple[int, int, int, int]] = {}
    base_center_shifts: dict[str, int] = {}
    for gname, master in masters.glyphs.items():
        adv_k = metrics[gname][0]
        match width_mode:
            case CaleucheWidthMode.PROPORTIONAL:
                d_adv = master.advance - adv_k
            case CaleucheWidthMode.SERIF_ONLY:
                d_adv = 0
        base_center_shifts[gname] = int(round(0.5 * d_adv))
        match master:
            case SimpleGlyphMaster(regular_coords=c_pts, regular_ytlc=c_ytlc):
                base_coords, _ = glyf._getCoordinatesAndControls(gname, metrics)
                k_pts = [(int(round(x)), int(round(y))) for x, y in base_coords[:-4]]
                if k_pts and c_pts:
                    ky1 = max(y for _, y in k_pts)
                    cy1 = max(y for _, y in c_pts)
                    cy1_ytlc = max(y + dy for (_, y), (_, dy) in zip(c_pts, c_ytlc))
                    anchor_policy = TopAnchorShiftPolicy.resolve(
                        ky1, cy1, c_xh_reg, c_cap_reg
                    )
                    dy_cale = anchor_policy.compute_dy(ky1, cy1)
                    dy_ytlc = cy1_ytlc - cy1
                    dx_cale = int(round(0.5 * d_adv + dy_cale * tan_italic))
                    dx_ytlc = int(round(dy_ytlc * tan_italic))
                    base_top_shifts[gname] = (dx_cale, dy_cale, dx_ytlc, dy_ytlc)
            case EmptyGlyphMaster() | CompositeGlyphMaster():
                pass

    for gname, master in masters.glyphs.items():
        if isinstance(master, CompositeGlyphMaster) and glyf[gname].isComposite() and glyf[gname].components:
            comp0 = glyf[gname].components[0].glyphName
            if comp0 in base_top_shifts:
                _, dy_cale, dx_ytlc, dy_ytlc = base_top_shifts[comp0]
                match width_mode:
                    case CaleucheWidthMode.PROPORTIONAL:
                        d_adv = master.advance - metrics[gname][0]
                    case CaleucheWidthMode.SERIF_ONLY:
                        d_adv = 0
                dx_cale = int(round(0.5 * d_adv + dy_cale * tan_italic))
                base_top_shifts[gname] = (dx_cale, dy_cale, dx_ytlc, dy_ytlc)

    # Group new MarkBasePos and PairPos devices by their region index tuple
    grouped_rows: dict[tuple[int, ...], list[list[int]]] = {}
    grouped_dedup: dict[tuple[int, ...], dict[tuple[int, ...], int]] = {}
    pending_devices: list[PendingDevice] = []

    def register_row(
        ref: DeviceRef,
        extra_regs: list[int],
        extra_vals: tuple[int, ...],
    ) -> tuple[tuple[int, ...], int] | None:
        if not any(extra_vals):
            return None
        match ref:
            case NoDeviceRef():
                reg_tuple = tuple(extra_regs)
                row_tuple = extra_vals
            case ExistingDeviceRef(outer=outer, inner=inner):
                orig_regs, orig_rows = orig_snapshots[outer]
                orig_row = orig_rows[inner]
                reg_tuple = tuple(
                    orig_regs + [r + orig_count for r in orig_regs] + extra_regs
                )
                row_tuple = tuple(orig_row + [-v for v in orig_row] + list(extra_vals))

        dedup = grouped_dedup.setdefault(reg_tuple, {})
        row_idx = dedup.get(row_tuple)
        if row_idx is None:
            rows = grouped_rows.setdefault(reg_tuple, [])
            row_idx = len(rows)
            dedup[row_tuple] = row_idx
            rows.append(list(row_tuple))
        return reg_tuple, row_idx

    for lookup in font["GPOS"].table.LookupList.Lookup:
        for st in lookup.SubTable:
            sub = st.ExtSubTable if lookup.LookupType == 9 else st
            kind = sub.LookupType if lookup.LookupType == 9 else lookup.LookupType
            if kind == 2:
                records = (
                    (pvr.Value1 for ps in sub.PairSet for pvr in ps.PairValueRecord)
                    if sub.Format == 1
                    else (c2.Value1 for c1 in sub.Class1Record for c2 in c1.Class2Record)
                )
                match width_mode:
                    case CaleucheWidthMode.PROPORTIONAL:
                        # Cancel Kerberos PairPos XAdvance and XAdvDevice linearly along CALE 0 -> 100
                        for val in records:
                            if val is None:
                                continue
                            x_adv = int(getattr(val, "XAdvance", 0) or 0)
                            if x_adv != 0:
                                x_ref = _device_ref(getattr(val, "XAdvDevice", None))
                                registered = register_row(x_ref, [r_cale], (-x_adv,))
                                if registered is not None:
                                    reg_tuple, row_idx = registered
                                    pending_devices.append(
                                        PendingValueDevice(val, "XAdvDevice", reg_tuple, row_idx)
                                    )
                    case CaleucheWidthMode.SERIF_ONLY:
                        # Preserve PairPos XAdvance and XAdvDevice invariant across CALE 0..100
                        for val in records:
                            if val is None:
                                continue
                            match _device_ref(getattr(val, "XAdvDevice", None)):
                                case ExistingDeviceRef(outer=outer, inner=inner):
                                    orig_regs, orig_rows = orig_snapshots[outer]
                                    reg_tuple = tuple(orig_regs)
                                    row_tuple = tuple(orig_rows[inner])
                                    dedup = grouped_dedup.setdefault(reg_tuple, {})
                                    row_idx = dedup.get(row_tuple)
                                    if row_idx is None:
                                        rows = grouped_rows.setdefault(reg_tuple, [])
                                        row_idx = len(rows)
                                        dedup[row_tuple] = row_idx
                                        rows.append(list(row_tuple))
                                    pending_devices.append(
                                        PendingValueDevice(val, "XAdvDevice", reg_tuple, row_idx)
                                    )
                                case NoDeviceRef():
                                    pass
            elif kind == 4:
                for gname, rec in zip(sub.BaseCoverage.glyphs, sub.BaseArray.BaseRecord):
                    for anchor in rec.BaseAnchor:
                        if anchor is None:
                            continue
                        if anchor.YCoordinate >= 350 and gname in base_top_shifts:
                            dx_c, dy_c, dx_y, dy_y = base_top_shifts[gname]
                        else:
                            dx_c = base_center_shifts.get(gname, 0)
                            dy_c = dx_y = dy_y = 0
                        half_dx_y = int(round(0.5 * dx_y))
                        half_dy_y = int(round(0.5 * dy_y))
                        x_ref = _device_ref(getattr(anchor, "XDeviceTable", None))
                        y_ref = _device_ref(getattr(anchor, "YDeviceTable", None))
                        reg_x = register_row(
                            x_ref, cale_region_indices, (dx_c, dx_y, half_dx_y, -half_dx_y)
                        )
                        if reg_x is not None:
                            pending_devices.append(
                                PendingAnchorDevice(anchor, "XDeviceTable", reg_x[0], reg_x[1])
                            )
                        reg_y = register_row(
                            y_ref, cale_region_indices, (dy_c, dy_y, half_dy_y, -half_dy_y)
                        )
                        if reg_y is not None:
                            pending_devices.append(
                                PendingAnchorDevice(anchor, "YDeviceTable", reg_y[0], reg_y[1])
                            )

    match width_mode:
        case CaleucheWidthMode.PROPORTIONAL:
            _inject_caleuche_optical_kerning(
                font, masters, r_cale, register_row, pending_devices
            )
        case CaleucheWidthMode.SERIF_ONLY:
            pass

    outer_by_reg_tuple: dict[tuple[int, ...], int] = {}
    for reg_tuple, rows in grouped_rows.items():
        outer_idx = len(new_var_data)
        outer_by_reg_tuple[reg_tuple] = outer_idx
        new_var_data.append(buildVarData(list(reg_tuple), rows, optimize=True))

    for pending in pending_devices:
        match pending:
            case PendingAnchorDevice(
                anchor=anchor, attr=attr, reg_tuple=reg_tuple, inner_idx=inner_idx
            ):
                anchor.Format = 3
                if getattr(anchor, "XDeviceTable", None) is None:
                    anchor.XDeviceTable = None
                if getattr(anchor, "YDeviceTable", None) is None:
                    anchor.YDeviceTable = None
                setattr(
                    anchor, attr, _make_var_device(outer_by_reg_tuple[reg_tuple], inner_idx)
                )
            case PendingValueDevice(
                value_record=val, attr=attr, reg_tuple=reg_tuple, inner_idx=inner_idx
            ):
                setattr(
                    val, attr, _make_var_device(outer_by_reg_tuple[reg_tuple], inner_idx)
                )

    vs.VarData = new_var_data
    vs.VarDataCount = len(new_var_data)


ITALIC_W_SPLICE_ORDER: tuple[int, ...] = (
    tuple(range(0, 69))
    + tuple(range(135, 157))
    + tuple(range(121, 136))
    + tuple(range(68, 121))
)


def _splice_italic_w_to_single_contour(font: TTFont) -> None:
    """Splice 2-contour Kerberos Italic 'w' ([120, 156]) into a 1-contour outline ([158]).

    This preserves 100% optical identicalness at CALE=0 across all 51 design-space
    corners while matching Caleuche Italic 'w's 1-contour topology.
    """
    glyf = font["glyf"]
    if "w" not in glyf:
        return
    g = glyf["w"]
    if g.numberOfContours != 2 or list(g.endPtsOfContours) != [120, 156]:
        return

    metrics = font["hmtx"].metrics
    base, ctrl = glyf._getCoordinatesAndControls("w", metrics)
    variations = font["gvar"].variations.get("w", [])
    for variation in variations:
        if None in variation.coordinates:
            variation.calcInferredDeltas(base, ctrl.endPts)
        reordered = [
            (int(round(variation.coordinates[i][0])), int(round(variation.coordinates[i][1])))
            for i in ITALIC_W_SPLICE_ORDER
        ]
        phantoms = [
            (int(round(c[0])), int(round(c[1]))) if c is not None else (0, 0)
            for c in variation.coordinates[-4:]
        ]
        variation.coordinates = reordered + phantoms

    orig_coords = list(g.coordinates)
    orig_flags = list(g.flags)
    g.coordinates = GlyphCoordinates([orig_coords[i] for i in ITALIC_W_SPLICE_ORDER])
    g.flags = array("B", [orig_flags[i] for i in ITALIC_W_SPLICE_ORDER])
    g.numberOfContours = 1
    g.endPtsOfContours = [len(ITALIC_W_SPLICE_ORDER) - 1]
    if "maxp" in font:
        font["maxp"].maxPoints = max(font["maxp"].maxPoints, len(ITALIC_W_SPLICE_ORDER))


@dataclass(frozen=True)
class ItalicFfSpliceSpec:
    orig_end_pts: tuple[int, ...]
    i_out: int
    i_hole: int


class ItalicFfLigatureSplice(enum.Enum):
    F_F = ("f_f", ItalicFfSpliceSpec((195, 222), 129, 206))
    F_F_I = ("f_f_i", ItalicFfSpliceSpec((246, 273), 165, 257))
    F_F_L = ("f_f_l", ItalicFfSpliceSpec((266, 293), 202, 277))
    F_F_B = ("f_f_b", ItalicFfSpliceSpec((233, 260, 295), 192, 244))
    F_F_H = ("f_f_h", ItalicFfSpliceSpec((285, 312), 229, 296))
    F_F_K = ("f_f_k", ItalicFfSpliceSpec((275, 302, 316), 214, 286))
    F_F_J = ("f_f_j", ItalicFfSpliceSpec((164, 180), 118, 175))

    def __init__(self, glyph_name: str, spec: ItalicFfSpliceSpec) -> None:
        self.glyph_name = glyph_name
        self.spec = spec


def _splice_italic_ff_ligatures(font: TTFont) -> None:
    """Splice the enclosed f-f arch hole into the outer contour via a zero-width bridge at CALE=0.

    This preserves 100% optical identicalness at CALE=0 while allowing open-loop
    Caleuche f-f ligatures at CALE=100 without any hole contour collapsing.
    """
    glyf = font["glyf"]
    metrics = font["hmtx"].metrics
    gvar = font["gvar"].variations if "gvar" in font else {}
    for member in ItalicFfLigatureSplice:
        gname = member.glyph_name
        spec = member.spec
        if gname not in glyf:
            continue
        g = glyf[gname]
        if g.numberOfContours <= 1 or tuple(g.endPtsOfContours) != spec.orig_end_pts:
            continue
        end_pts = list(g.endPtsOfContours)
        e0 = end_pts[0]
        s1 = e0 + 1
        e1 = end_pts[1]
        order_0 = (
            list(range(0, spec.i_out + 1))
            + list(range(spec.i_hole, e1 + 1))
            + list(range(s1, spec.i_hole + 1))
            + list(range(spec.i_out, e0 + 1))
        )
        order_rest = list(range(e1 + 1, end_pts[-1] + 1))
        order = order_0 + order_rest

        base, ctrl = glyf._getCoordinatesAndControls(gname, metrics)
        for variation in gvar.get(gname, []):
            if None in variation.coordinates:
                variation.calcInferredDeltas(base, ctrl.endPts)
            reordered = [
                (int(round(variation.coordinates[i][0])), int(round(variation.coordinates[i][1])))
                for i in order
            ]
            phantoms = [
                (int(round(c[0])), int(round(c[1]))) if c is not None else (0, 0)
                for c in variation.coordinates[-4:]
            ]
            variation.coordinates = reordered + phantoms

        orig_coords = list(g.coordinates)
        orig_flags = list(g.flags)
        g.coordinates = GlyphCoordinates([orig_coords[i] for i in order])
        g.flags = array("B", [orig_flags[i] for i in order])
        new_ends = [len(order_0) - 1] + [ep + 2 for ep in end_pts[2:]]
        g.numberOfContours = len(new_ends)
        g.endPtsOfContours = new_ends
        if "maxp" in font:
            font["maxp"].maxPoints = max(font["maxp"].maxPoints, len(order))


def add_caleuche_axis(
    font: TTFont,
    style_stem: str,
    width_mode: CaleucheWidthMode = CaleucheWidthMode.PROPORTIONAL,
) -> None:
    """Inject the CALE (0..100) axis into `font` in place."""
    c_style = CaleucheStyle.from_stem(style_stem)
    masters = load_style_masters(c_style, width_mode=width_mode)

    # Ensure gvar is decompiled while fvar still has 4 axes.
    _ = font["gvar"].variations
    if c_style is CaleucheStyle.ITALIC:
        _splice_italic_w_to_single_contour(font)
        _splice_italic_ff_ligatures(font)

    axis = Axis()
    axis.axisTag = TAG
    axis.axisNameID = font["name"].addName(NAME)
    axis.minValue = MIN_VALUE
    axis.defaultValue = DEFAULT_VALUE
    axis.maxValue = MAX_VALUE
    axis.flags = 0
    font["fvar"].axes.append(axis)
    for instance in font["fvar"].instances:
        instance.coordinates[TAG] = DEFAULT_VALUE

    if "avar" in font:
        font["avar"].segments[TAG] = {-1.0: -1.0, 0.0: 0.0, 1.0: 1.0}

    _update_gvar(font, masters, width_mode=width_mode)
    _update_hvar(font, masters, width_mode=width_mode)
    _update_mvar(font, masters)
    _update_gdef_and_gpos(font, masters, width_mode=width_mode)
