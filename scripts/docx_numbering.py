"""Render WordprocessingML automatic paragraph numbering.

Word stores list labels separately from paragraph text.  Reading only ``w:t``
nodes therefore drops labels such as ``Điều 1.``, even though Word displays
them.  This module resolves direct and style-inherited ``w:numPr`` properties
and renders the corresponding ``w:lvlText`` prefix in document order.

The implementation is intentionally strict: a paragraph that references a
missing numbering definition raises instead of silently changing legal text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from lxml import etree


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
W_VAL = f"{{{W_NS}}}val"


class NumberingResolutionError(ValueError):
    """The OOXML numbering graph is incomplete or inconsistent."""


@dataclass(frozen=True)
class LevelDefinition:
    start: int
    number_format: str
    level_text: str
    suffix: str
    restart_after_level: int | None


@dataclass(frozen=True)
class NumberingInstance:
    abstract_id: str
    start_overrides: Mapping[int, int]
    level_overrides: Mapping[int, LevelDefinition]


@dataclass(frozen=True)
class StyleNumbering:
    based_on: str | None
    num_id: str | None
    level: int | None


def _child_value(element: etree._Element | None, path: str) -> str | None:
    if element is None:
        return None
    child = element.find(path, namespaces=NS)
    return child.get(W_VAL) if child is not None else None


def _integer(value: str | None, *, default: int, field: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise NumberingResolutionError(
            f"Invalid integer for {field}: {value!r}"
        ) from exc


def _roman(value: int) -> str:
    if value <= 0 or value >= 4000:
        return str(value)
    pairs = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    result: list[str] = []
    remaining = value
    for amount, glyph in pairs:
        count, remaining = divmod(remaining, amount)
        result.extend([glyph] * count)
    return "".join(result)


def _letters(value: int) -> str:
    if value <= 0:
        return str(value)
    result: list[str] = []
    remaining = value
    while remaining:
        remaining, offset = divmod(remaining - 1, 26)
        result.append(chr(ord("A") + offset))
    return "".join(reversed(result))


def _format_number(value: int, number_format: str) -> str:
    if number_format == "decimal":
        return str(value)
    if number_format == "decimalZero":
        return f"{value:02d}"
    if number_format == "upperRoman":
        return _roman(value)
    if number_format == "lowerRoman":
        return _roman(value).lower()
    if number_format == "upperLetter":
        return _letters(value)
    if number_format == "lowerLetter":
        return _letters(value).lower()
    if number_format == "none":
        return ""
    raise NumberingResolutionError(
        f"Unsupported Word numbering format: {number_format!r}"
    )


def _parse_level(element: etree._Element, level: int) -> LevelDefinition:
    start = _integer(
        _child_value(element, "w:start"),
        default=1,
        field=f"level {level} start",
    )
    number_format = _child_value(element, "w:numFmt") or "decimal"
    level_text = _child_value(element, "w:lvlText") or f"%{level + 1}."
    suffix = _child_value(element, "w:suff") or "tab"
    raw_restart = _child_value(element, "w:lvlRestart")
    if raw_restart == "0":
        restart_after_level = None
    elif raw_restart is None:
        restart_after_level = level - 1 if level > 0 else None
    else:
        # OOXML stores this value one-based.
        restart_after_level = max(0, _integer(
            raw_restart,
            default=1,
            field=f"level {level} restart",
        ) - 1)
    return LevelDefinition(
        start=start,
        number_format=number_format,
        level_text=level_text,
        suffix=suffix,
        restart_after_level=restart_after_level,
    )


class WordNumberingResolver:
    """Stateful renderer for one DOCX package."""

    def __init__(
        self,
        numbering_root: etree._Element | None,
        styles_root: etree._Element | None,
    ) -> None:
        self._abstract_levels: dict[str, dict[int, LevelDefinition]] = {}
        self._instances: dict[str, NumberingInstance] = {}
        self._styles: dict[str, StyleNumbering] = {}
        self._counters: dict[str, dict[int, int]] = {}
        self.rendered_prefixes = 0
        self._load_styles(styles_root)
        self._load_numbering(numbering_root)

    @property
    def numbering_instances(self) -> int:
        return len(self._instances)

    def _load_styles(self, root: etree._Element | None) -> None:
        if root is None:
            return
        for style in root.xpath("./w:style", namespaces=NS):
            style_id = style.get(f"{{{W_NS}}}styleId")
            if not style_id:
                continue
            num_pr = style.find("w:pPr/w:numPr", namespaces=NS)
            self._styles[style_id] = StyleNumbering(
                based_on=_child_value(style, "w:basedOn"),
                num_id=_child_value(num_pr, "w:numId"),
                level=_integer(
                    _child_value(num_pr, "w:ilvl"),
                    default=0,
                    field=f"style {style_id} level",
                ) if _child_value(num_pr, "w:ilvl") is not None else None,
            )

    def _load_numbering(self, root: etree._Element | None) -> None:
        if root is None:
            return
        for abstract in root.xpath("./w:abstractNum", namespaces=NS):
            abstract_id = abstract.get(f"{{{W_NS}}}abstractNumId")
            if abstract_id is None:
                continue
            levels: dict[int, LevelDefinition] = {}
            for level_node in abstract.xpath("./w:lvl", namespaces=NS):
                level = _integer(
                    level_node.get(f"{{{W_NS}}}ilvl"),
                    default=0,
                    field=f"abstract {abstract_id} level",
                )
                levels[level] = _parse_level(level_node, level)
            self._abstract_levels[abstract_id] = levels

        for num in root.xpath("./w:num", namespaces=NS):
            num_id = num.get(f"{{{W_NS}}}numId")
            abstract_id = _child_value(num, "w:abstractNumId")
            if num_id is None or abstract_id is None:
                continue
            start_overrides: dict[int, int] = {}
            level_overrides: dict[int, LevelDefinition] = {}
            for override in num.xpath("./w:lvlOverride", namespaces=NS):
                level = _integer(
                    override.get(f"{{{W_NS}}}ilvl"),
                    default=0,
                    field=f"num {num_id} override level",
                )
                raw_start = _child_value(override, "w:startOverride")
                if raw_start is not None:
                    start_overrides[level] = _integer(
                        raw_start,
                        default=1,
                        field=f"num {num_id} start override",
                    )
                level_node = override.find("w:lvl", namespaces=NS)
                if level_node is not None:
                    level_overrides[level] = _parse_level(level_node, level)
            self._instances[num_id] = NumberingInstance(
                abstract_id=abstract_id,
                start_overrides=start_overrides,
                level_overrides=level_overrides,
            )

    def _style_numbering(self, style_id: str | None) -> tuple[str | None, int | None]:
        num_id: str | None = None
        level: int | None = None
        visited: set[str] = set()
        current = style_id
        while current:
            if current in visited:
                raise NumberingResolutionError(
                    f"Cycle in Word style inheritance at {current!r}"
                )
            visited.add(current)
            style = self._styles.get(current)
            if style is None:
                break
            if num_id is None and style.num_id is not None:
                num_id = style.num_id
            if level is None and style.level is not None:
                level = style.level
            current = style.based_on
        return num_id, level

    def _paragraph_numbering(
        self, paragraph: etree._Element
    ) -> tuple[str, int] | None:
        p_pr = paragraph.find("w:pPr", namespaces=NS)
        style_id = _child_value(p_pr, "w:pStyle")
        style_num_id, style_level = self._style_numbering(style_id)
        num_pr = p_pr.find("w:numPr", namespaces=NS) if p_pr is not None else None
        direct_num_id = _child_value(num_pr, "w:numId")
        raw_direct_level = _child_value(num_pr, "w:ilvl")
        num_id = direct_num_id if direct_num_id is not None else style_num_id
        if num_id in {None, "0"}:
            return None
        level = (
            _integer(
                raw_direct_level,
                default=0,
                field=f"paragraph numId {num_id} level",
            )
            if raw_direct_level is not None
            else (style_level if style_level is not None else 0)
        )
        return num_id, level

    def _level_definition(self, num_id: str, level: int) -> LevelDefinition:
        instance = self._instances.get(num_id)
        if instance is None:
            raise NumberingResolutionError(
                f"Paragraph references missing numbering instance {num_id}"
            )
        if level in instance.level_overrides:
            return instance.level_overrides[level]
        levels = self._abstract_levels.get(instance.abstract_id)
        if levels is None or level not in levels:
            raise NumberingResolutionError(
                f"Numbering instance {num_id} has no definition for level {level}"
            )
        return levels[level]

    def _start(self, num_id: str, level: int) -> int:
        instance = self._instances[num_id]
        definition = self._level_definition(num_id, level)
        return int(instance.start_overrides.get(level, definition.start))

    def _advance(self, num_id: str, level: int) -> dict[int, int]:
        counters = self._counters.setdefault(num_id, {})
        counters[level] = (
            counters[level] + 1
            if level in counters
            else self._start(num_id, level)
        )
        for deeper in list(counters):
            if deeper <= level:
                continue
            definition = self._level_definition(num_id, deeper)
            restart = definition.restart_after_level
            if restart is not None and level <= restart:
                counters.pop(deeper, None)
        return counters

    def prefix_for(self, paragraph: etree._Element) -> str:
        numbering = self._paragraph_numbering(paragraph)
        if numbering is None:
            return ""
        num_id, level = numbering
        definition = self._level_definition(num_id, level)
        counters = self._advance(num_id, level)

        def replacement(match: re.Match[str]) -> str:
            referenced_level = int(match.group(1)) - 1
            if referenced_level < 0:
                return match.group(0)
            value = counters.get(referenced_level)
            if value is None:
                value = self._start(num_id, referenced_level)
            referenced_definition = self._level_definition(
                num_id, referenced_level
            )
            return _format_number(value, referenced_definition.number_format)

        prefix = re.sub(r"%([1-9])", replacement, definition.level_text)
        prefix = prefix.replace("\t", " ").strip()
        if prefix:
            self.rendered_prefixes += 1
        return prefix
