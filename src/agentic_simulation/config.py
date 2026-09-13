"""Validated configuration for world generation and visualization."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """A frozen configuration model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class GenerationConfig(StrictModel):
    """Parameters for deterministic procedural world generation."""

    smoothing_passes: int = Field(default=4, ge=0, le=20)
    water_level: float = Field(default=0.28, ge=0.0, le=1.0)
    mountain_level: float = Field(default=0.80, ge=0.0, le=1.0)
    forest_moisture: float = Field(default=0.58, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_levels(self) -> GenerationConfig:
        if self.water_level >= self.mountain_level:
            raise ValueError("water_level must be lower than mountain_level")
        return self


class ResourceConfig(StrictModel):
    """Generation and regeneration settings for one resource type."""

    initial_nodes: int = Field(ge=0)
    capacity: float = Field(gt=0.0)
    initial_fill_min: float = Field(default=0.35, ge=0.0, le=1.0)
    initial_fill_max: float = Field(default=1.0, ge=0.0, le=1.0)
    regeneration_rate: float = Field(default=0.05, ge=0.0)

    @model_validator(mode="after")
    def check_fill_range(self) -> ResourceConfig:
        if self.initial_fill_min > self.initial_fill_max:
            raise ValueError("initial_fill_min cannot exceed initial_fill_max")
        return self


class ResourcesConfig(StrictModel):
    food: ResourceConfig = ResourceConfig(
        initial_nodes=250,
        capacity=100.0,
        initial_fill_min=0.45,
        regeneration_rate=0.15,
    )
    water: ResourceConfig = ResourceConfig(
        initial_nodes=80,
        capacity=160.0,
        initial_fill_min=0.70,
        regeneration_rate=0.25,
    )


class WorldConfig(StrictModel):
    """Complete deterministic world configuration."""

    schema_version: Literal[1] = 1
    seed: int = Field(default=42, ge=0)
    width: int = Field(default=100, ge=4, le=4096)
    height: int = Field(default=100, ge=4, le=4096)
    day_length_ticks: int = Field(default=240, ge=2)
    diurnal_temperature_amplitude: float = Field(default=4.0, ge=0.0, le=30.0)
    generation: GenerationConfig = GenerationConfig()
    resources: ResourcesConfig = ResourcesConfig()


class VisualizationConfig(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    ticks_per_second: float = Field(default=8.0, gt=0.0, le=1000.0)
    broadcast_every_ticks: int = Field(default=1, ge=1)


class AppConfig(StrictModel):
    world: WorldConfig = WorldConfig()
    visualization: VisualizationConfig = VisualizationConfig()


def load_config(path: str | Path) -> AppConfig:
    """Load and strictly validate an application YAML configuration."""

    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    return AppConfig.model_validate(raw)
