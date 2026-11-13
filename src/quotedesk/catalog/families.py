"""Product families for the synthetic Musterhandel catalog.

A family is a product line with option axes. The generator expands every combination of
options into one Product. All brand names are invented.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quotedesk.catalog.models import AttrValue, Category, Unit

Options = Mapping[str, Any]
D = Decimal

# Invented manufacturers and the prefix used for their part numbers.
MANUFACTURER_PREFIX: dict[str, str] = {
    "Kabelwerk Nord": "KWN",
    "Voltmann": "VM",
    "Elektra Süd": "ES",
    "Lichtform": "LF",
    "Rohrtec": "RT",
    "Aquaflux": "AQX",
    "Installa": "INS",
}


def de(value: Decimal | int) -> str:
    """German number format: Decimal('1.5') -> '1,5'."""
    return str(value).replace(".", ",")


@dataclass(frozen=True)
class Family:
    key: str
    category: Category
    unit: Unit
    # Ordered option axes. Two axes are special:
    #   "manufacturer" -> Product.manufacturer
    #   "pack_size"    -> Product.pack_size (otherwise default_pack_size)
    axes: dict[str, tuple[Any, ...]]
    attributes: Callable[[Options], dict[str, AttrValue]]
    name_de: Callable[[Options], str]
    name_en: Callable[[Options], str]
    base_price: Callable[[Options], Decimal]
    default_pack_size: int = 1


COLOURS_EN = {
    "grau": "grey", "schwarz": "black", "blau": "blue", "braun": "brown",
    "grün-gelb": "green-yellow", "rot": "red", "weiß": "white", "reinweiß": "pure white",
    "alpinweiß": "alpine white", "anthrazit": "anthracite", "edelstahl": "stainless steel",
    "schwarz matt": "matt black",
}  # fmt: skip


def _area(size: str) -> int:
    a, b = size.split("x")
    return int(a) * int(b)


_SERIES_PRICE: dict[str, Decimal] = {"Basic": D("3.90"), "Linea": D("6.80"), "Forma": D("11.50")}
_SOCKET_FACTOR: dict[str, Decimal] = {
    "Schuko-Steckdose": D("1"),
    "Schuko-Steckdose mit Klappdeckel": D("1.6"),
    "USB-Steckdose": D("4.2"),
}
_SOCKET_EN: dict[str, str] = {
    "Schuko-Steckdose": "Schuko socket",
    "Schuko-Steckdose mit Klappdeckel": "Schuko socket with hinged lid",
    "USB-Steckdose": "USB socket",
}
_SWITCH_EN: dict[str, str] = {
    "Ausschalter": "On/off switch",
    "Wechselschalter": "Two-way switch",
    "Kreuzschalter": "Intermediate switch",
    "Serienschalter": "Series switch",
    "Taster": "Push button",
}
_SHAPE_EN: dict[str, str] = {
    "Winkel 90°": "elbow 90°",
    "Bogen 45°": "bend 45°",
    "T-Stück": "tee",
    "Muffe": "coupling",
    "Endkappe": "end cap",
    "Übergang AG": "adapter male thread",
    "Übergang IG": "adapter female thread",
    "Schiebemuffe": "slip coupling",
}
_MATERIAL_EN: dict[str, str] = {
    "Kupfer": "copper",
    "Edelstahl": "stainless steel",
    "C-Stahl": "carbon steel",
    "Messing vernickelt": "nickel-plated brass",
}
_MATERIAL_FACTOR: dict[str, Decimal] = {
    "Kupfer": D("1"),
    "Edelstahl": D("2.4"),
    "C-Stahl": D("1.3"),
}


CS = (D("1.5"), D("2.5"), D("4"), D("6"), D("10"), D("16"))


def _cable(type_: str, de_word: str, en_word: str, per_mm2: str) -> Family:
    return Family(
        key=type_.lower(),
        category=Category.CABLE,
        unit=Unit.METRE,
        axes={
            "manufacturer": ("Kabelwerk Nord", "Installa"),
            "cores": (3, 4, 5, 7),
            "cs": CS,
            "pack_size": (50, 100),
        },
        attributes=lambda o: {
            "type": type_,
            "cores": D(o["cores"]),
            "cross_section_mm2": o["cs"],
        },
        name_de=lambda o: (
            f"{de_word} {type_} {o['cores']}x{de(o['cs'])} mm² Ring {o['pack_size']} m"
        ),
        name_en=lambda o: f"{en_word} {type_} {o['cores']}x{o['cs']} mm², {o['pack_size']} m ring",
        base_price=lambda o: D(per_mm2) * o["cores"] * o["cs"] + D("0.20"),
    )


FAMILIES: tuple[Family, ...] = (
    _cable("NYM-J", "Mantelleitung", "Sheathed cable", "0.11"),
    _cable("NYY-J", "Erdkabel", "Underground cable", "0.14"),
    _cable("H07RN-F", "Gummischlauchleitung", "Rubber flexible cable", "0.17"),
    Family(
        key="h07v-k",
        category=Category.WIRE,
        unit=Unit.METRE,
        axes={
            "manufacturer": ("Kabelwerk Nord", "Installa"),
            "cs": (D("0.75"), D("1"), *CS),
            "colour": ("schwarz", "blau", "grün-gelb", "braun", "grau", "rot"),
        },
        attributes=lambda o: {
            "type": "H07V-K",
            "cross_section_mm2": o["cs"],
            "colour": o["colour"],
        },
        name_de=lambda o: f"Aderleitung H07V-K {de(o['cs'])} mm² {o['colour']} Ring 100 m",
        name_en=lambda o: (
            f"Single-core wire H07V-K {o['cs']} mm² {COLOURS_EN[o['colour']]}, 100 m ring"
        ),
        base_price=lambda o: D("0.09") * o["cs"] + D("0.06"),
        default_pack_size=100,
    ),
    Family(
        key="gland",
        category=Category.CABLE_GLAND,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Installa", "Elektra Süd"),
            "thread": ("M12", "M16", "M20", "M25", "M32", "M40", "M50", "M63"),
            "material": ("Kunststoff", "Messing"),
            "colour": ("grau", "schwarz"),
        },
        attributes=lambda o: {
            "thread": o["thread"],
            "material": o["material"],
            "colour": o["colour"],
            "ip_rating": "IP68",
        },
        name_de=lambda o: f"Kabelverschraubung {o['thread']} {o['material']} {o['colour']} IP68",
        name_en=lambda o: (
            f"Cable gland {o['thread']} "
            f"{'plastic' if o['material'] == 'Kunststoff' else 'brass'} "
            f"{COLOURS_EN[o['colour']]} IP68"
        ),
        base_price=lambda o: (
            (D("0.40") + D("0.04") * int(o["thread"][1:]))
            * (D("4") if o["material"] == "Messing" else D("1"))
        ),
    ),
    Family(
        key="box",
        category=Category.JUNCTION_BOX,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Installa", "Elektra Süd", "Voltmann"),
            "ip": ("IP54", "IP55", "IP65", "IP66"),
            "size": ("75x75", "100x100", "150x110", "190x150"),
            "colour": ("grau", "weiß"),
        },
        attributes=lambda o: {"ip_rating": o["ip"], "size_mm": o["size"], "colour": o["colour"]},
        name_de=lambda o: f"Abzweigdose AP {o['size']} mm {o['ip']} {o['colour']}",
        name_en=lambda o: (
            f"Surface junction box {o['size']} mm {o['ip']} {COLOURS_EN[o['colour']]}"
        ),
        base_price=lambda o: D("2.10") + D("0.0004") * _area(o["size"]),
    ),
    Family(
        key="mcb",
        category=Category.CIRCUIT_BREAKER,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Voltmann", "Elektra Süd", "Installa"),
            "char": ("B", "C"),
            "current": (6, 10, 13, 16, 20, 25, 32, 40, 50, 63),
            "poles": (1, 3),
        },
        attributes=lambda o: {
            "characteristic": o["char"],
            "rated_current_a": D(o["current"]),
            "poles": D(o["poles"]),
            "breaking_capacity_ka": D("6"),
        },
        name_de=lambda o: (
            f"Leitungsschutzschalter {o['char']}{o['current']} {o['poles']}-polig 6 kA"
        ),
        name_en=lambda o: f"Circuit breaker {o['char']}{o['current']} {o['poles']}-pole 6 kA",
        base_price=lambda o: (D("3.20") + D("0.05") * o["current"]) * o["poles"],
    ),
    Family(
        key="rcd",
        category=Category.RCD,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Voltmann", "Elektra Süd", "Installa"),
            "current": (25, 40, 63),
            "residual": (30, 300),
            "poles": (2, 4),
            "rcd_type": ("A", "B"),
        },
        attributes=lambda o: {
            "rated_current_a": D(o["current"]),
            "residual_current_ma": D(o["residual"]),
            "poles": D(o["poles"]),
            "rcd_type": o["rcd_type"],
        },
        name_de=lambda o: (
            f"FI-Schutzschalter {o['current']} A {o['residual']} mA {o['poles']}-polig "
            f"Typ {o['rcd_type']}"
        ),
        name_en=lambda o: (
            f"Residual current device {o['current']} A {o['residual']} mA {o['poles']}-pole "
            f"type {o['rcd_type']}"
        ),
        base_price=lambda o: (
            (D("24") + D("0.3") * o["current"])
            * (D("1.6") if o["poles"] == 4 else D("1"))
            * (D("8") if o["rcd_type"] == "B" else D("1"))
        ),
    ),
    Family(
        key="socket",
        category=Category.SOCKET,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Elektra Süd", "Voltmann"),
            "series": ("Linea", "Forma", "Basic"),
            "variant": ("Schuko-Steckdose", "Schuko-Steckdose mit Klappdeckel", "USB-Steckdose"),
            "colour": ("reinweiß", "alpinweiß", "anthrazit", "edelstahl", "schwarz matt"),
        },
        attributes=lambda o: {
            "variant": o["variant"],
            "series": o["series"],
            "colour": o["colour"],
        },
        name_de=lambda o: f"{o['variant']} UP Serie {o['series']} {o['colour']}",
        name_en=lambda o: (
            f"{_SOCKET_EN[o['variant']]} flush-mounted, {o['series']} series, "
            f"{COLOURS_EN[o['colour']]}"
        ),
        base_price=lambda o: _SERIES_PRICE[o["series"]] * _SOCKET_FACTOR[o["variant"]],
    ),
    Family(
        key="switch",
        category=Category.SWITCH,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Elektra Süd", "Voltmann"),
            "series": ("Linea", "Forma", "Basic"),
            "variant": (
                "Ausschalter",
                "Wechselschalter",
                "Kreuzschalter",
                "Serienschalter",
                "Taster",
            ),
            "colour": ("reinweiß", "alpinweiß", "anthrazit", "edelstahl", "schwarz matt"),
        },
        attributes=lambda o: {
            "variant": o["variant"],
            "series": o["series"],
            "colour": o["colour"],
        },
        name_de=lambda o: f"{o['variant']} UP Serie {o['series']} {o['colour']}",
        name_en=lambda o: (
            f"{_SWITCH_EN[o['variant']]} flush-mounted, {o['series']} series, "
            f"{COLOURS_EN[o['colour']]}"
        ),
        base_price=lambda o: _SERIES_PRICE[o["series"]] * D("0.9"),
    ),
    Family(
        key="panel",
        category=Category.LIGHTING,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Lichtform", "Elektra Süd"),
            "size": ("600x600", "625x625", "1200x300"),
            "cct": (3000, 4000, 6500),
            "power": (30, 36, 40),
        },
        attributes=lambda o: {
            "variant": "LED-Panel",
            "size_mm": o["size"],
            "colour_temperature_k": D(o["cct"]),
            "power_w": D(o["power"]),
        },
        name_de=lambda o: f"LED-Panel {o['size']} mm {o['power']} W {o['cct']} K",
        name_en=lambda o: f"LED panel {o['size']} mm {o['power']} W {o['cct']} K",
        base_price=lambda o: D("22") + D("0.8") * o["power"],
    ),
    Family(
        key="downlight",
        category=Category.LIGHTING,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Lichtform", "Elektra Süd"),
            "diameter": (68, 90, 120),
            "cct": (2700, 3000, 4000),
            "power": (5, 7, 10),
            "ip": ("IP20", "IP44"),
        },
        attributes=lambda o: {
            "variant": "LED-Downlight",
            "diameter_mm": D(o["diameter"]),
            "colour_temperature_k": D(o["cct"]),
            "power_w": D(o["power"]),
            "ip_rating": o["ip"],
        },
        name_de=lambda o: (
            f"LED-Einbaustrahler Ø{o['diameter']} mm {o['power']} W {o['cct']} K {o['ip']}"
        ),
        name_en=lambda o: (
            f"LED recessed downlight Ø{o['diameter']} mm {o['power']} W {o['cct']} K {o['ip']}"
        ),
        base_price=lambda o: D("9") + D("1.1") * o["power"] + (D("3") if o["ip"] == "IP44" else 0),
    ),
    Family(
        key="conduit",
        category=Category.CONDUIT,
        unit=Unit.METRE,
        axes={
            "manufacturer": ("Installa", "Rohrtec"),
            "variant": ("flexibel", "starr"),
            "diameter": (16, 20, 25, 32, 40, 50),
            "colour": ("grau", "schwarz"),
            "pack_size": (25, 50),
        },
        attributes=lambda o: {
            "variant": o["variant"],
            "diameter_mm": D(o["diameter"]),
            "colour": o["colour"],
        },
        name_de=lambda o: (
            f"Installationsrohr {o['variant']} M{o['diameter']} {o['colour']} "
            f"{'Ring' if o['variant'] == 'flexibel' else 'Bund'} {o['pack_size']} m"
        ),
        name_en=lambda o: (
            f"{'Flexible' if o['variant'] == 'flexibel' else 'Rigid'} conduit "
            f"M{o['diameter']} {COLOURS_EN[o['colour']]}, {o['pack_size']} m"
        ),
        base_price=lambda o: D("0.012") * o["diameter"] + D("0.05"),
    ),
    Family(
        key="press",
        category=Category.PRESS_FITTING,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Rohrtec", "Aquaflux", "Installa"),
            "shape": (
                "Winkel 90°",
                "Bogen 45°",
                "T-Stück",
                "Muffe",
                "Endkappe",
                "Übergang AG",
                "Übergang IG",
                "Schiebemuffe",
            ),
            "diameter": (12, 15, 18, 22, 28, 35, 42, 54),
            "material": ("Kupfer", "Edelstahl", "C-Stahl"),
        },  # fmt: skip
        attributes=lambda o: {
            "shape": o["shape"],
            "diameter_mm": D(o["diameter"]),
            "material": o["material"],
        },
        name_de=lambda o: f"Pressfitting {o['shape']} {o['diameter']} mm {o['material']}",
        name_en=lambda o: (
            f"Press fitting {_SHAPE_EN[o['shape']]} {o['diameter']} mm "
            f"{_MATERIAL_EN[o['material']]}"
        ),
        base_price=lambda o: (
            (D("1.5") + D("0.12") * o["diameter"]) * _MATERIAL_FACTOR[o["material"]]
        ),
    ),
    Family(
        key="ballvalve",
        category=Category.BALL_VALVE,
        unit=Unit.PIECE,
        axes={
            "manufacturer": ("Aquaflux", "Rohrtec", "Installa"),
            "dn": (10, 15, 20, 25, 32, 40, 50),
            "connection": ("IG/IG", "IG/AG"),
            "handle": ("Hebel", "Flügel"),
            "material": ("Messing vernickelt", "Edelstahl"),
        },
        attributes=lambda o: {
            "dn": D(o["dn"]),
            "connection": o["connection"],
            "handle": o["handle"],
            "material": o["material"],
        },
        name_de=lambda o: f"Kugelhahn DN{o['dn']} {o['connection']} {o['handle']} {o['material']}",
        name_en=lambda o: (
            f"Ball valve DN{o['dn']} {o['connection'].replace('IG', 'F').replace('AG', 'M')} "
            f"{'lever' if o['handle'] == 'Hebel' else 'butterfly'} handle "
            f"{_MATERIAL_EN[o['material']]}"
        ),
        base_price=lambda o: (
            (D("4") + D("0.45") * o["dn"]) * (D("2.2") if o["material"] == "Edelstahl" else D("1"))
        ),
    ),
)
