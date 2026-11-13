"""Discover and sample documented Windows performance-counter readings."""
from __future__ import annotations

import hashlib
import platform
import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider
from backend.winapi.cpu import system_cpu_percent
from backend.winapi.pdh import PdhCounterSample, PdhSession, PdhStatusError

_THERMAL_NOTE = (
    "ACPI thermal zone: may not equal the CPU core temperature"
)
_UNMEASURED_ZERO_NOTE = (
    "reported 0; this meter may not be measured on this hardware"
)


@dataclass(frozen=True, slots=True)
class _CounterDefinition:
    """Describe one requested English PDH path and its confirmed interpretation."""

    path: str
    category: str
    label: str
    kind: Literal[
        "temperature",
        "passive-limit",
        "throttle-reasons",
        "frequency-percent",
        "power",
        "gpu-utilization",
        "gpu-memory",
    ]


@dataclass(frozen=True, slots=True)
class _CounterBinding:
    """Connect one discovered counter instance to its registered sensor."""

    sensor_id: str
    handle: Any
    path: str
    instance: str
    kind: str
    explanation: str
    zero_may_be_unmeasured: bool = False


# Microsoft Learn: About Performance Counters and PdhGetCounterInfoW.
# Exact paths below are the user-requested Windows English counter names;
# each unit is accepted only when the local PDH explanation confirms it.
_COUNTER_DEFINITIONS = (
    _CounterDefinition(
        r"\Thermal Zone Information(*)\Temperature",
        "thermal",
        "ACPI thermal-zone temperature",
        "temperature",
    ),
    _CounterDefinition(
        r"\Thermal Zone Information(*)\% Passive Limit",
        "thermal",
        "ACPI thermal-zone passive limit",
        "passive-limit",
    ),
    _CounterDefinition(
        r"\Thermal Zone Information(*)\Throttle Reasons",
        "thermal",
        "ACPI thermal-zone throttle reason present",
        "throttle-reasons",
    ),
    _CounterDefinition(
        r"\Processor Information(_Total)\% of Maximum Frequency",
        "cpu",
        "CPU frequency as percent of maximum",
        "frequency-percent",
    ),
    _CounterDefinition(
        r"\Energy Meter(*)\Power",
        "power",
        "Energy-meter power",
        "power",
    ),
    _CounterDefinition(
        r"\Power Meter(*)\Power",
        "power",
        "Power-meter power",
        "power",
    ),
    _CounterDefinition(
        r"\GPU Engine(*)\Utilization Percentage",
        "gpu",
        "GPU engine utilization",
        "gpu-utilization",
    ),
    _CounterDefinition(
        r"\GPU Adapter Memory(*)\Dedicated Usage",
        "gpu",
        "GPU adapter dedicated memory usage",
        "gpu-memory",
    ),
    _CounterDefinition(
        r"\GPU Adapter Memory(*)\Shared Usage",
        "gpu",
        "GPU adapter shared memory usage",
        "gpu-memory",
    ),
)


def _instance_suffix(instance: str) -> str:
    """Create a stable, readable sensor suffix from an arbitrary PDH instance."""
    readable = re.sub(r"[^a-z0-9]+", "-", instance.casefold()).strip("-")
    digest = hashlib.blake2s(instance.encode("utf-8"), digest_size=4).hexdigest()
    return f"{readable[:24] or 'instance'}-{digest}"


def _unit_for(
    kind: str,
    explanation: str | None,
) -> tuple[str | None, str | None]:
    """Accept only units that are explicitly stated by the PDH explanation."""
    text = (explanation or "").casefold()
    if kind == "temperature":
        if "temperature" in text and "kelvin" in text:
            return "C", None
        return None, "PDH explanation did not confirm Kelvin temperature units"
    if kind == "passive-limit":
        if "limit" in text and "%" in text:
            return "%", None
        return None, "PDH explanation did not confirm percentage units"
    if kind == "throttle-reasons":
        if "throttle reasons" in text and "limiting performance" in text:
            return "bool", None
        return None, "PDH explanation did not confirm throttle-reason meaning"
    if kind == "frequency-percent":
        if "percentage" in text and "maximum frequency" in text:
            return "%", None
        return None, "PDH explanation did not confirm percent-of-maximum units"
    if kind == "power":
        if "power" in text and "milliwatt" in text:
            return "W", None
        return None, "PDH explanation did not confirm milliwatt units"
    if kind == "gpu-utilization":
        if "percentage" in text:
            return "%", None
        return None, "PDH explanation did not confirm percentage units"
    if kind == "gpu-memory":
        if "memory used by the adapter" in text:
            return None, (
                "PDH explanation does not state a unit; the reading was not converted"
            )
        return None, "PDH explanation did not confirm GPU-memory meaning"
    return None, "Unknown PDH counter interpretation"


class PdhProvider(SensorProvider):
    """Expose available thermal, power, processor, and GPU PDH counters."""

    name = "pdh"
    poll_categories = ("thermal", "cpu", "power", "gpu", "fan")

    def __init__(self) -> None:
        self._session: PdhSession | None = None
        self._initialized = False
        self._sensors: list[Sensor] = []
        self._bindings: list[_CounterBinding] = []
        self._values: dict[str, float | None] = {}
        self._states: dict[str, tuple[SensorState, str | None]] = {}
        self._diagnostics: dict[str, tuple[str | None, str | None]] = {}
        self._status = "not_initialized"

    def discover(self) -> list[Sensor]:
        """Probe requested counters once and register only exposed instances."""
        if self._initialized:
            return list(self._sensors)
        self._initialized = True
        self._sensors = []
        self._bindings = []
        self._values = {}
        self._states = {}
        self._diagnostics = {}
        self._status = "available"
        if platform.system() != "Windows":
            self._set_all_unsupported("PDH performance counters require Windows")
            return list(self._sensors)

        try:
            self._session = PdhSession()
        except OSError as error:
            self._set_all_unsupported(str(error))
            self._status = "unsupported"
            return list(self._sensors)

        for definition in _COUNTER_DEFINITIONS:
            self._discover_counter(definition)
        self._add_fan_sensor()
        self._add_throttling_sensor()
        return list(self._sensors)

    def read(self) -> dict[str, float | None]:
        """Collect at least two PDH samples and preserve missing-data states."""
        if not self._initialized:
            self.discover()
        self._values = {}
        self._states = {
            sensor.id: (sensor.state, sensor.error_message)
            for sensor in self._sensors
            if sensor.state not in (
                SensorState.NOT_INITIALIZED,
                SensorState.AVAILABLE,
            )
        }
        if self._session is None:
            return dict(self._values)
        try:
            ready = self._session.collect(minimum_interval_s=1.0)
        except OSError as error:
            self._set_binding_errors(error)
            self._status = "error"
            return dict(self._values)
        if not ready:
            for binding in self._bindings:
                self._states[binding.sensor_id] = (
                    SensorState.NOT_INITIALIZED,
                    "no data yet; PDH rate counters require two samples at least 1 s apart",
                )
                self._values[binding.sensor_id] = None
            self._update_throttling_estimate({})
            return dict(self._values)

        sampled: dict[str, float | None] = {}
        for binding in self._bindings:
            try:
                sample = self._session.counter_sample(binding.handle)
            except OSError as error:
                if binding.instance.casefold().startswith("pid_"):
                    state = SensorState.TEMPORARILY_DISCONNECTED
                    message = "Process ended; its counter instance is gone"
                else:
                    state = (
                        SensorState.UNSUPPORTED
                        if isinstance(error, PdhStatusError)
                        and error.state == "object or counter does not exist"
                        else SensorState.ERROR
                    )
                    message = str(error)
                self._states[binding.sensor_id] = (state, message)
                sampled[binding.sensor_id] = None
                self._diagnostics[binding.sensor_id] = (
                    binding.path,
                    _pdh_error_code(error),
                )
                continue
            value, state, message = self._convert_sample(binding, sample)
            sampled[binding.sensor_id] = value
            if state is not None:
                self._states[binding.sensor_id] = (state, message)

        self._values = sampled
        self._update_throttling_estimate(sampled)
        return dict(self._values)

    def sensor_states(self) -> dict[str, tuple[SensorState, str | None]]:
        return dict(self._states)

    def diagnostics(self) -> dict[str, tuple[str | None, str | None]]:
        return dict(self._diagnostics)

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._status,
            "counter_count": len(self._bindings),
            "counter_explanations_read": self._session is not None,
            "fan": "unsupported",
        }

    def shutdown(self) -> None:
        """Release the persistent PDH query."""
        if self._session is None:
            return
        session = self._session
        self._session = None
        try:
            session.close()
        except OSError:
            self._status = "error"

    def _discover_counter(self, definition: _CounterDefinition) -> None:
        """Expand a counter path, validate its explanation, and register rows."""
        assert self._session is not None
        try:
            if "(*)" in definition.path:
                entries = self._session.add_expanded_counter(definition.path)
            else:
                handle = self._session.add_english_counter(definition.path)
                entries = [("_Total", handle)]
        except OSError as error:
            self._add_missing_counter(definition, error)
            return

        if not entries:
            self._add_missing_counter(
                definition,
                OSError("PDH reported no counter instances"),
            )
            return
        for instance, handle in entries:
            try:
                explanation = self._session.counter_explanation(handle) or ""
            except OSError as error:
                sensor_id = self._sensor_id(definition, instance)
                message = f"Could not read PDH counter explanation: {error}"
                self._sensors.append(
                    self._make_sensor(
                        sensor_id,
                        f"{definition.label} ({instance})",
                        definition.category,
                        "unknown",
                        definition.kind,
                        state=SensorState.UNAVAILABLE,
                        error_message=message,
                        api_path=definition.path,
                    )
                )
                self._states[sensor_id] = (SensorState.UNAVAILABLE, message)
                self._diagnostics[sensor_id] = (
                    definition.path,
                    _pdh_error_code(error),
                )
                continue
            unit, interpretation_error = _unit_for(definition.kind, explanation)
            sensor_id = self._sensor_id(definition, instance)
            if unit is None:
                api_path = (
                    f"PdhGetCounterInfoW explanation for {definition.path}"
                )
                message = interpretation_error
                if definition.kind == "gpu-memory":
                    message = (
                        "PdhGetCounterInfoW succeeded, but its explanation "
                        "does not state a unit; the reading was not converted"
                    )
                sensor = self._make_sensor(
                    sensor_id,
                    f"{definition.label} ({instance})",
                    definition.category,
                    "unknown",
                    definition.kind,
                    state=SensorState.UNAVAILABLE,
                    error_message=message,
                    note=explanation or None,
                    api_path=api_path,
                )
                self._sensors.append(sensor)
                self._states[sensor_id] = (
                    SensorState.UNAVAILABLE,
                    message,
                )
                self._diagnostics[sensor_id] = (api_path, None)
                continue

            note = _THERMAL_NOTE if definition.kind == "temperature" else None
            reliability = (
                "reported-by-firmware"
                if definition.kind in ("temperature", "passive-limit", "throttle-reasons")
                else "reported-by-windows"
            )
            sensor = self._make_sensor(
                sensor_id,
                f"{definition.label} ({instance})",
                definition.category,
                unit,
                definition.kind,
                reliability=reliability,
                note=note,
                api_path=definition.path,
            )
            self._sensors.append(sensor)
            self._bindings.append(
                _CounterBinding(
                    sensor_id=sensor_id,
                    handle=handle,
                    path=definition.path,
                    instance=instance,
                    kind=definition.kind,
                    explanation=explanation,
                    zero_may_be_unmeasured=(
                        definition.kind == "power"
                        and (
                            instance.casefold().endswith("dram")
                            or instance.casefold() == "_total"
                        )
                    ),
                )
            )
            self._diagnostics[sensor_id] = (
                f"PdhGetFormattedCounterValue({definition.path})",
                None,
            )

    def _add_missing_counter(
        self,
        definition: _CounterDefinition,
        error: OSError,
    ) -> None:
        """Register a row for a requested counter that Windows does not expose."""
        sensor_id = self._sensor_id(definition, "not-found")
        status = (
            error.state
            if isinstance(error, PdhStatusError)
            else "object or counter does not exist"
        )
        state = (
            SensorState.UNSUPPORTED
            if status == "object or counter does not exist"
            else SensorState.UNAVAILABLE
        )
        message = f"{error}; counter path: {definition.path}"
        self._sensors.append(
            self._make_sensor(
                sensor_id,
                definition.label,
                definition.category,
                "unknown",
                definition.kind,
                state=state,
                error_message=message,
                api_path=definition.path,
            )
        )
        self._states[sensor_id] = (state, message)
        self._diagnostics[sensor_id] = (
            definition.path,
            _pdh_error_code(error),
        )

    def _add_fan_sensor(self) -> None:
        """Report fan RPM unsupported without querying an external program."""
        sensor_id = "fan.rpm"
        message = "Windows does not expose fan speed on this computer"
        self._sensors.append(
            self._make_sensor(
                sensor_id,
                "Fan speed",
                "fan",
                "RPM",
                "fan",
                state=SensorState.UNSUPPORTED,
                error_message=message,
                api_path="No Windows fan-speed API or PDH counter is queried",
            )
        )
        self._states[sensor_id] = (SensorState.UNSUPPORTED, message)
        self._diagnostics[sensor_id] = (
            "No Windows fan-speed API or PDH counter is queried",
            None,
        )

    def _add_throttling_sensor(self) -> None:
        """Register a conservative estimate from real processor and thermal inputs."""
        sensor_id = "cpu.throttling.estimated"
        message = "Unavailable until load, frequency, and a thermal limiter are readable"
        self._sensors.append(
            self._make_sensor(
                sensor_id,
                "CPU throttling (estimated)",
                "cpu",
                "bool",
                "throttling",
                reliability="calculated-estimate",
                state=SensorState.UNAVAILABLE,
                error_message=message,
                note="Requires high load, reduced maximum frequency, and a thermal limiter",
                api_path=(
                    "PDH frequency/thermal counters and GetSystemTimes CPU utilization"
                ),
            )
        )
        self._states[sensor_id] = (SensorState.UNAVAILABLE, message)
        self._diagnostics[sensor_id] = (
            "PdhGetFormattedCounterValue(\\Processor Information(_Total)"
            "\\% of Maximum Frequency); "
            "PdhGetFormattedCounterValue(\\Thermal Zone Information(*)"
            "\\% Passive Limit or \\Thermal Zone Information(*)"
            "\\Throttle Reasons); GetSystemTimes",
            None,
        )

    def _update_throttling_estimate(
        self,
        sampled: dict[str, float | None],
    ) -> None:
        """Estimate throttling only when each required live input is available."""
        sensor_id = "cpu.throttling.estimated"
        frequency = next(
            (
                sampled.get(binding.sensor_id)
                for binding in self._bindings
                if binding.kind == "frequency-percent"
            ),
            None,
        )
        passive_limits = [
            sampled.get(binding.sensor_id)
            for binding in self._bindings
            if binding.kind == "passive-limit"
        ]
        throttle_flags = [
            sampled.get(binding.sensor_id)
            for binding in self._bindings
            if binding.kind == "throttle-reasons"
        ]
        passive = [value for value in passive_limits if value is not None]
        throttle = [value for value in throttle_flags if value is not None]
        try:
            cpu_load = system_cpu_percent()
        except (OSError, RuntimeError):
            cpu_load = None
        if frequency is None or cpu_load is None or not passive and not throttle:
            missing_inputs: list[str] = []
            if frequency is None:
                missing_inputs.append(
                    r"\Processor Information(_Total)\% of Maximum Frequency "
                    "returned no valid sample"
                )
            if cpu_load is None:
                missing_inputs.append(
                    "GetSystemTimes has not produced two meaningful samples "
                    "at least 1 s apart"
                )
            if not passive and not throttle:
                missing_inputs.extend(
                    [
                        r"\Thermal Zone Information(*)\% Passive Limit "
                        "returned no valid sample",
                        r"\Thermal Zone Information(*)\Throttle Reasons "
                        "returned no valid sample",
                    ]
                )
            self._values[sensor_id] = None
            self._states[sensor_id] = (
                SensorState.UNAVAILABLE,
                "CPU throttling estimate unavailable: "
                + "; ".join(missing_inputs),
            )
            return
        limiter_active = any(value < 100.0 for value in passive) or any(
            value > 0 for value in throttle
        )
        self._values[sensor_id] = float(
            cpu_load >= 80.0 and frequency < 100.0 and limiter_active
        )
        self._states.pop(sensor_id, None)

    def _convert_sample(
        self,
        binding: _CounterBinding,
        sample: PdhCounterSample,
    ) -> tuple[float | None, SensorState | None, str | None]:
        """Convert confirmed units and preserve PDH no-data/error states."""
        if sample.state == "no data yet":
            return (
                None,
                SensorState.NOT_INITIALIZED,
                "no data yet; PDH counter requires two samples at least 1 s apart",
            )
        if sample.state == "object or counter does not exist":
            return (
                None,
                SensorState.UNSUPPORTED,
                f"PDH counter does not exist: {binding.path}",
            )
        if sample.state != "valid" or sample.value is None:
            return None, SensorState.UNAVAILABLE, "PDH returned no valid counter value"

        value = sample.value
        if binding.zero_may_be_unmeasured and value == 0.0:
            return None, SensorState.UNAVAILABLE, _UNMEASURED_ZERO_NOTE
        if binding.kind == "temperature":
            return value - 273.15, None, None
        if binding.kind == "power":
            return value / 1000.0, None, None
        if binding.kind == "throttle-reasons":
            return float(value != 0.0), None, None
        if binding.kind == "gpu-memory":
            return (
                None,
                SensorState.UNAVAILABLE,
                "PdhGetCounterInfoW succeeded, but its explanation does not state "
                "a unit; the reading was not converted",
            )
        return value, None, None

    def _set_all_unsupported(self, message: str) -> None:
        """Register every required row as unsupported when PDH cannot be opened."""
        for definition in _COUNTER_DEFINITIONS:
            sensor_id = self._sensor_id(definition, "not-found")
            self._sensors.append(
                self._make_sensor(
                    sensor_id,
                    definition.label,
                    definition.category,
                    "unknown",
                    definition.kind,
                    state=SensorState.UNSUPPORTED,
                    error_message=message,
                    api_path=definition.path,
                )
            )
            self._states[sensor_id] = (SensorState.UNSUPPORTED, message)
            self._diagnostics[sensor_id] = (definition.path, None)
        self._add_fan_sensor()
        self._add_throttling_sensor()

    def _set_binding_errors(self, error: OSError) -> None:
        """Apply one failed query collection to every bound PDH sensor."""
        for binding in self._bindings:
            self._states[binding.sensor_id] = (SensorState.ERROR, str(error))
            self._values[binding.sensor_id] = None
            self._diagnostics[binding.sensor_id] = (
                binding.path,
                _pdh_error_code(error),
            )

    @staticmethod
    def _sensor_id(definition: _CounterDefinition, instance: str) -> str:
        """Return a stable ID for one counter instance."""
        suffix = _instance_suffix(instance)
        return f"{definition.category}.{definition.kind}.{suffix}"

    @staticmethod
    def _make_sensor(
        sensor_id: str,
        name: str,
        category: str,
        unit: str,
        kind: str,
        reliability: str = "reported-by-windows",
        state: SensorState = SensorState.NOT_INITIALIZED,
        error_message: str | None = None,
        note: str | None = None,
        api_path: str | None = None,
    ) -> Sensor:
        """Create a sensor record with its source and discovery state."""
        if kind == "temperature":
            reliability = "reported-by-firmware"
        return Sensor(
            id=sensor_id,
            name=name,
            category=category,
            unit=unit,
            source="pdh",
            reliability=reliability,
            state=state,
            error_message=error_message,
            note=note,
            api_path=api_path,
        )


def _pdh_error_code(error: OSError) -> str | None:
    if isinstance(error, PdhStatusError):
        return f"0x{error.errno & 0xFFFFFFFF:08X}"
    return str(error.errno) if error.errno is not None else None
