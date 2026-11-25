"""Industry pack schema and loader.

A pack is a YAML file in this folder. Adding `packs/<key>.yaml` that passes this schema
is all it takes to support a new industry: no code changes.
"""
from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

PACK_DIR = Path(__file__).parent


class Stage(BaseModel):
    key: str
    name: str
    group: str
    machine_type: str
    minutes_per_unit: float
    description: str = ""
    default: bool = True


class Parameter(BaseModel):
    key: str
    label: str
    value: float | str
    unit: str = ""
    description: str = ""
    default: bool = True


class Formula(BaseModel):
    key: str
    ref: str  # dotted path to an engine function, checked at load time
    description: str = ""

    @field_validator("ref")
    @classmethod
    def ref_must_import(cls, v: str) -> str:
        mod, _, fn = v.rpartition(".")
        if not mod or not hasattr(importlib.import_module(mod), fn):
            raise ValueError(f"formula ref '{v}' does not point to an engine function")
        return v


class Standard(BaseModel):
    key: str
    name: str
    category: Literal["restricted_chemical", "effluent", "quality", "safety", "other"]
    limit: str
    reference: str
    note: str = ""
    default: bool = True


class GlossaryTerm(BaseModel):
    term: str
    meaning: str
    local_terms: list[str] = Field(default_factory=list)  # e.g. Hindi words used on the shop floor


class Question(BaseModel):
    key: str
    question: str
    type: Literal["number", "text", "choice"] = "number"
    unit: str = ""
    options: list[str] = Field(default_factory=list)


class ProductType(BaseModel):
    key: str
    name: str
    material_per_unit: float
    material_unit: str


class Pack(BaseModel):
    key: str
    name: str
    version: str
    unit_of_output: str
    units_of_measure: list[str]
    stages: list[Stage]
    parameters: list[Parameter]
    product_types: list[ProductType] = Field(default_factory=list)
    formulas: list[Formula]
    standards: list[Standard] = Field(default_factory=list)
    glossary: list[GlossaryTerm] = Field(default_factory=list)
    excess_issue_reasons: list[str] = Field(default_factory=list)
    onboarding_questions: list[Question]

    @model_validator(mode="after")
    def unique_keys(self) -> "Pack":
        for name, items in (("stages", self.stages), ("parameters", self.parameters)):
            keys = [i.key for i in items]
            if len(keys) != len(set(keys)):
                raise ValueError(f"duplicate keys in {name}")
        return self

    def param(self, key: str) -> Parameter | None:
        return next((p for p in self.parameters if p.key == key), None)


def list_packs() -> list[str]:
    return sorted(p.stem for p in PACK_DIR.glob("*.yaml"))


@lru_cache
def load_pack(key: str) -> Pack:
    path = PACK_DIR / f"{key}.yaml"
    if not path.exists():
        raise ValueError(f"Unknown industry pack '{key}'. Available: {', '.join(list_packs())}")
    return Pack.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def validate_all() -> dict[str, Pack]:
    """Load every pack; raises on the first invalid one. Called at server startup."""
    return {k: load_pack(k) for k in list_packs()}
