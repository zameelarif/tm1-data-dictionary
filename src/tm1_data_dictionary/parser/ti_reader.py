"""Read a TI process from TM1 and expose its parts as clean, structured data.

This is the parser's **input layer**. It contains no parsing logic - it simply fetches a
process via TM1py and repackages its pieces into our own dataclasses, insulating the rest
of the parser from TM1py's specific attribute names (``prolog_procedure``,
``datasource_ascii_delimiter_char``, etc.).

Everything downstream (block segmentation, reference extraction, ...) works against our
stable :class:`TIProcess`, not TM1py directly - so a future TM1py change touches only here.
It is fully testable with a fake service; no real TM1 is needed.

TM1py reference (verified against tm1py.org): ``service.processes.get_all_names()`` and
``service.processes.get(name)`` returning a ``Process`` with ``prolog_procedure`` /
``metadata_procedure`` / ``data_procedure`` / ``epilog_procedure``, ``parameters``,
``variables``, and ``datasource_*`` attributes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from tm1_data_dictionary.tm1_client import TM1Client


@dataclass(frozen=True)
class TIDatasource:
    """The datasource configuration of a TI process (design-time values)."""

    type: str
    delimiter: str = ""
    quote_character: str = ""
    header_records: int = 0
    decimal_separator: str = "."
    thousands_separator: str = ","
    uses_unicode: bool = True
    name_for_server: str = ""
    name_for_client: str = ""
    query: str = ""
    view: str = ""
    subset: str = ""


@dataclass(frozen=True)
class TIVariable:
    """A declared TI variable (a source column, or an internal work variable)."""

    name: str
    var_type: str  # "String" or "Numeric" (as TM1py reports it)
    position: int  # 1-based column position; 0 if not a source column


@dataclass(frozen=True)
class TIParameter:
    """A TI process parameter."""

    name: str
    param_type: str
    default_value: object = ""


@dataclass(frozen=True)
class TIProcess:
    """A TI process, repackaged into a tidy shape for the parser."""

    name: str
    prolog: str
    metadata: str
    data: str
    epilog: str
    datasource: TIDatasource
    variables: tuple[TIVariable, ...] = ()
    parameters: tuple[TIParameter, ...] = ()
    has_security_access: bool = False

    def iter_blocks(self) -> Iterator[tuple[str, str]]:
        """Yield ``(block_name, text)`` for each procedure block, in execution order."""
        yield ("Prolog", self.prolog)
        yield ("Metadata", self.metadata)
        yield ("Data", self.data)
        yield ("Epilog", self.epilog)

    @property
    def variable_count(self) -> int:
        return len(self.variables)

    @property
    def parameter_count(self) -> int:
        return len(self.parameters)


# --------------------------------------------------------------------------- #
# Mapping from a TM1py Process object to our TIProcess
# --------------------------------------------------------------------------- #


def _datasource_from_process(proc: object) -> TIDatasource:
    """Build a TIDatasource from a TM1py Process object's datasource_* attributes."""
    return TIDatasource(
        type=str(getattr(proc, "datasource_type", "None")),
        delimiter=str(getattr(proc, "datasource_ascii_delimiter_char", "")),
        quote_character=str(getattr(proc, "datasource_ascii_quote_character", "")),
        header_records=int(getattr(proc, "datasource_ascii_header_records", 0) or 0),
        decimal_separator=str(getattr(proc, "datasource_ascii_decimal_separator", ".")),
        thousands_separator=str(getattr(proc, "datasource_ascii_thousand_separator", ",")),
        uses_unicode=bool(getattr(proc, "datasource_uses_unicode", True)),
        name_for_server=str(getattr(proc, "datasource_data_source_name_for_server", "")),
        name_for_client=str(getattr(proc, "datasource_data_source_name_for_client", "")),
        query=str(getattr(proc, "datasource_query", "")),
        view=str(getattr(proc, "datasource_view", "")),
        subset=str(getattr(proc, "datasource_subset", "")),
    )


def _variables_from_process(proc: object) -> tuple[TIVariable, ...]:
    """Build TIVariables from a TM1py Process object's variables list (dict or object)."""
    raw = getattr(proc, "variables", None) or []
    result: list[TIVariable] = []
    for idx, var in enumerate(raw, start=1):
        if isinstance(var, dict):
            name = str(var.get("Name", var.get("name", "")))
            var_type = str(var.get("Type", var.get("type", "String")))
            position = int(var.get("Position", var.get("position", idx)) or idx)
        else:
            name = str(getattr(var, "name", ""))
            var_type = str(getattr(var, "type", "String"))
            position = int(getattr(var, "position", idx) or idx)
        if name:
            result.append(TIVariable(name=name, var_type=var_type, position=position))
    return tuple(result)


def _parameters_from_process(proc: object) -> tuple[TIParameter, ...]:
    """Build TIParameters from a TM1py Process object's parameters list (dict or object)."""
    raw = getattr(proc, "parameters", None) or []
    result: list[TIParameter] = []
    for param in raw:
        if isinstance(param, dict):
            name = str(param.get("Name", param.get("name", "")))
            ptype = str(param.get("Type", param.get("type", "String")))
            default = param.get("Value", param.get("value", ""))
        else:
            name = str(getattr(param, "name", ""))
            ptype = str(getattr(param, "type", "String"))
            default = getattr(param, "value", "")
        if name:
            result.append(TIParameter(name=name, param_type=ptype, default_value=default))
    return tuple(result)


def process_from_tm1py(proc: object) -> TIProcess:
    """Convert a TM1py ``Process`` object into our :class:`TIProcess`."""
    return TIProcess(
        name=str(getattr(proc, "name", "")),
        prolog=str(getattr(proc, "prolog_procedure", "") or ""),
        metadata=str(getattr(proc, "metadata_procedure", "") or ""),
        data=str(getattr(proc, "data_procedure", "") or ""),
        epilog=str(getattr(proc, "epilog_procedure", "") or ""),
        datasource=_datasource_from_process(proc),
        variables=_variables_from_process(proc),
        parameters=_parameters_from_process(proc),
        has_security_access=bool(getattr(proc, "has_security_access", False)),
    )


# --------------------------------------------------------------------------- #
# The reader
# --------------------------------------------------------------------------- #


@dataclass
class TIReader:
    """Reads TI processes from TM1 via a :class:`TM1Client`, returning :class:`TIProcess`."""

    client: TM1Client
    _names_cache: list[str] | None = field(default=None, repr=False)

    def list_process_names(self, *, refresh: bool = False) -> list[str]:
        """Return all process names in the instance (sorted). Cached after first call."""
        if self._names_cache is None or refresh:
            names = self.client.service.processes.get_all_names()
            self._names_cache = sorted(names)
        return list(self._names_cache)

    def exists(self, name: str) -> bool:
        """Return True if a process with ``name`` exists."""
        return bool(self.client.service.processes.exists(name))

    def read(self, name: str) -> TIProcess:
        """Fetch a single process and return it as a :class:`TIProcess`."""
        proc = self.client.service.processes.get(name)
        return process_from_tm1py(proc)
