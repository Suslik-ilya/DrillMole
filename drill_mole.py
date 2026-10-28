"""
Крот-бур «Копатель» (проект DrillMole, стихия — земля).
Реализация по SPEC.md версии 1.0.
"""

from __future__ import annotations

import math
from typing import NamedTuple

__all__ = [
    "CrazyVehicleError",
    "DrillMoleError",
    "DrillMoleCannotSwim",
    "DrillMoleDrillWorn",
    "DrillMoleNoFuel",
    "DrillMoleNotOnSurface",
    "DrillMole",
]


class CrazyVehicleError(Exception):
    """Базовая ошибка всех «безумных» транспортных средств."""


class DrillMoleError(CrazyVehicleError):
    """Нарушение правил предметной области крота-бура."""


class DrillMoleCannotSwim(DrillMoleError):
    """Крот не умеет плавать: движение по воде запрещено."""


class DrillMoleDrillWorn(DrillMoleError):
    """Износ бура превысил бы 100 %: копать дальше нельзя."""


class DrillMoleNoFuel(DrillMoleError):
    """Топлива не хватает на запрошенное перемещение."""


class DrillMoleNotOnSurface(DrillMoleError):
    """Действие допустимо только на поверхности (замена бура)."""


#: Знаков после запятой для fuel, drill_wear и position_km (SPEC 3.5)
_PRECISION = 6
_EPSILON = 1e-9
#: Верхний предел топлива и износа бура
_MAX_LEVEL = 100.0
#: Минимальный диаметр бура, которым можно расколоть валун
_BOULDER_MIN_DIAMETER_M = 1.0

_WATER = "water"
_OBSTACLES = ("river", "wall", "boulder")


class _TerrainRule(NamedTuple):
    """Правила движения по одной среде (SPEC 3.4.1, таблица)."""

    speed_factor: float  # доля от max_speed_kmh
    fuel_per_km: float  # расход топлива на 1 км
    wear_per_km: float  # износ бура на 1 км, %
    underground: bool  # где окажется крот после движения


_TERRAIN_RULES: dict[str, _TerrainRule] = {
    "surface": _TerrainRule(1.0, 1.0, 0.0, False),
    "soil": _TerrainRule(0.5, 2.0, 1.0, True),
    "rock": _TerrainRule(0.25, 3.0, 5.0, True),
}
_KNOWN_TERRAINS = tuple(_TERRAIN_RULES) + (_WATER,)


def _check_number(value: object, name: str) -> float:
    """Проверить, что значение — конечное число (int/float, но не bool).

    Сначала проверяется тип (``TypeError``), затем конечность
    (``ValueError`` для NaN и бесконечностей) — порядок из SPEC 3.2.
    ``bool`` в Python является подклассом ``int``, поэтому исключается явно.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _check_positive(value: object, name: str) -> float:
    """Проверить, что значение — конечное число строго больше нуля."""
    number = _check_number(value, name)
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def _check_str(value: object, name: str) -> str:
    """Проверить, что значение — строка."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _round(value: float) -> float:
    """Округлить до 6 знаков, чтобы не копить ошибку float (SPEC 3.5)."""
    return round(value, _PRECISION)


class DrillMole:
    """Одноместная машина, которая ездит по поверхности и копает под землёй.

    Конструкционный элемент — бур: его диаметр определяет, можно ли расколоть
    валун, а износ растёт при движении под землёй и ограничивает копание.

    Все публичные атрибуты доступны только для чтения.
    """

    def __init__(
        self,
        name: str,
        drill_diameter_m: float,
        max_speed_kmh: float,
        fuel: float = 100.0,
    ) -> None:
        """Создать крота на поверхности с новым буром.

        Параметры проверяются по порядку в сигнатуре: для каждого сначала
        тип, затем значение.

        Raises:
            TypeError: неверный тип любого параметра (в том числе bool).
            ValueError: пустое имя; диаметр или скорость <= 0; fuel вне
                диапазона 0…100; NaN или бесконечность.
        """
        _check_str(name, "name")
        if not name.strip():
            raise ValueError("name must not be empty")
        diameter = _check_positive(drill_diameter_m, "drill_diameter_m")
        speed = _check_positive(max_speed_kmh, "max_speed_kmh")
        fuel_level = _check_number(fuel, "fuel")
        if not 0 <= fuel_level <= _MAX_LEVEL:
            raise ValueError("fuel must be between 0 and 100")

        self._name = name
        self._drill_diameter_m = diameter
        self._max_speed_kmh = speed
        self._fuel = fuel_level
        self._drill_wear = 0.0
        self._position_km = 0.0
        self._is_underground = False

    # атрибуты только для чтения (SPEC 3.3)

    @property
    def name(self) -> str:
        """Имя крота."""
        return self._name

    @property
    def drill_diameter_m(self) -> float:
        """Диаметр бура, м."""
        return self._drill_diameter_m

    @property
    def max_speed_kmh(self) -> float:
        """Скорость по поверхности, км/ч."""
        return self._max_speed_kmh

    @property
    def fuel(self) -> float:
        """Запас топлива, 0…100."""
        return self._fuel

    @property
    def drill_wear(self) -> float:
        """Износ бура, %, 0…100."""
        return self._drill_wear

    @property
    def position_km(self) -> float:
        """Суммарно пройденное расстояние, км."""
        return self._position_km

    @property
    def is_underground(self) -> bool:
        """Находится ли крот под землёй."""
        return self._is_underground

    # внутренние помощники

    @staticmethod
    def _check_move_arguments(
        distance_km: object, terrain: object
    ) -> tuple[float, str]:
        """Проверить расстояние и среду в порядке сигнатуры (SPEC 3.5).

        Для ``distance_km`` — тип, затем значение; для ``terrain`` — тип,
        затем принадлежность списку известных сред.
        """
        distance = _check_positive(distance_km, "distance_km")
        _check_str(terrain, "terrain")
        if terrain not in _KNOWN_TERRAINS:
            raise ValueError(f"unknown terrain: {terrain}")
        return distance, terrain  # type: ignore[return-value]

    @staticmethod
    def _fuel_cost(distance: float, terrain: str) -> float:
        """Расход топлива на перемещение (без округления); вода запрещена."""
        if terrain == _WATER:
            raise DrillMoleCannotSwim("drill mole cannot swim")
        return distance * _TERRAIN_RULES[terrain].fuel_per_km

    # методы (SPEC 3.4)

    def move(self, distance_km: float, terrain: str) -> float:
        """Переместить крота на расстояние в указанной среде.

        Порядок проверок: параметры → вода → износ бура → топливо.
        При любом исключении состояние не меняется: новые значения сначала
        вычисляются, и только потом записываются.

        Args:
            distance_km: расстояние в км, > 0.
            terrain: ``"surface"``, ``"soil"``, ``"rock"`` или ``"water"``.

        Returns:
            Затраченное время в часах (не округляется).

        Raises:
            TypeError: неверный тип параметра.
            ValueError: неположительное или нечисловое расстояние,
                неизвестная среда.
            DrillMoleCannotSwim: ``terrain == "water"``.
            DrillMoleDrillWorn: износ бура превысил бы 100.
            DrillMoleNoFuel: топлива не хватает.
        """
        distance, terrain = self._check_move_arguments(distance_km, terrain)
        # Внутри _fuel_cost выполняется и проверка воды.
        fuel_cost = self._fuel_cost(distance, terrain)
        rule = _TERRAIN_RULES[terrain]

        new_wear = self._drill_wear
        if rule.wear_per_km > 0:
            raw_wear = self._drill_wear + distance * rule.wear_per_km
            if raw_wear > _MAX_LEVEL:
                raise DrillMoleDrillWorn("drill is worn out")
            new_wear = _round(raw_wear)

        if self._fuel == 0 or fuel_cost - self._fuel > _EPSILON:
            raise DrillMoleNoFuel(
                f"not enough fuel: required {fuel_cost:g}, have {self._fuel:g}"
            )

        # Все проверки пройдены — теперь меняем состояние целиком.
        self._fuel = max(0.0, _round(self._fuel - fuel_cost))
        self._drill_wear = new_wear
        self._position_km = _round(self._position_km + distance)
        self._is_underground = rule.underground
        return distance / (self._max_speed_kmh * rule.speed_factor)

    def can_overcome(self, obstacle: str) -> bool:
        """Проверить проходимость препятствия, не меняя состояние.

        * ``"river"`` — только под землёй;
        * ``"wall"`` — под землёй и при ``drill_wear < 100``;
        * ``"boulder"`` — при ``drill_diameter_m >= 1.0``.

        Топливо не проверяется: расстояние до препятствия не задано.

        Raises:
            TypeError: ``obstacle`` не строка.
            ValueError: неизвестное препятствие.
        """
        _check_str(obstacle, "obstacle")
        if obstacle not in _OBSTACLES:
            raise ValueError(f"unknown obstacle: {obstacle}")
        if obstacle == "river":
            return self._is_underground
        if obstacle == "wall":
            return self._is_underground and self._drill_wear < _MAX_LEVEL
        return self._drill_diameter_m >= _BOULDER_MIN_DIAMETER_M

    def calculate_fuel(self, distance_km: float, terrain: str) -> float:
        """Рассчитать расход топлива на перемещение, не меняя состояние.

        Текущий запас топлива и износ бура не проверяются.

        Raises:
            TypeError: неверный тип параметра.
            ValueError: неположительное расстояние, неизвестная среда.
            DrillMoleCannotSwim: ``terrain == "water"``.
        """
        distance, terrain = self._check_move_arguments(distance_km, terrain)
        return self._fuel_cost(distance, terrain)

    def refuel(self, amount: float) -> None:
        """Заправить крота; запас не превышает 100.

        Raises:
            TypeError: ``amount`` не число (в том числе bool).
            ValueError: ``amount`` <= 0, NaN или бесконечность.
        """
        added = _check_positive(amount, "amount")
        self._fuel = min(_MAX_LEVEL, _round(self._fuel + added))

    def replace_drill(self) -> None:
        """Заменить бур: ``drill_wear`` становится 0, топливо не тратится.

        На поверхности замена исправного бура тоже допустима (состояние
        не меняется).

        Raises:
            DrillMoleNotOnSurface: крот под землёй (при любом износе).
        """
        if self._is_underground:
            raise DrillMoleNotOnSurface(
                "drill can be replaced only on surface"
            )
        self._drill_wear = 0.0

    def get_status(self) -> dict:
        """Вернуть новый независимый словарь с текущим состоянием."""
        return {
            "name": self._name,
            "fuel": self._fuel,
            "drill_wear": self._drill_wear,
            "position_km": self._position_km,
            "is_underground": self._is_underground,
        }