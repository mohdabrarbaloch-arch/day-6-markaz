"""Static reference data: regions, GPU SKUs, and models.

Everything here is configuration, not data. It lives in code so that a change to
"which regions exist" shows up in a diff and gets reviewed, rather than appearing
silently after someone edited a row through the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Region:
    """An availability region with a finite amount of GPU capacity."""

    code: str
    display_name: str
    gpu_capacity: int
    data_residency: tuple[str, ...]
    energy_usd_per_kwh: float
    skus: tuple[str, ...] = field(default_factory=tuple)

    def offers(self, sku_code: str) -> bool:
        """Whether this region sells the given SKU at all."""
        return sku_code in self.skus

    def permits(self, required: tuple[str, ...]) -> bool:
        """True when this region carries every tag the model requires.

        An empty requirement means "no residency constraint", which is the
        correct description of an open-weight model: it may run anywhere.
        """
        return all(tag in self.data_residency for tag in required)


@dataclass(frozen=True)
class Sku:
    """A purchasable GPU shape."""

    code: str
    display_name: str
    vram_gb: int
    hourly_usd: float


@dataclass(frozen=True)
class Model:
    """A deployable model, with the constraints that govern where it may run."""

    code: str
    display_name: str
    parameters_b: float
    min_vram_gb: int
    required_residency: tuple[str, ...]
    family: str


REGIONS: dict[str, Region] = {
    "ap-south": Region(
        code="ap-south",
        display_name="Asia South (Karachi)",
        gpu_capacity=12,
        data_residency=("apac", "pk"),
        energy_usd_per_kwh=0.11,
        skus=("t4-16", "a10-24", "l4-24", "a100-80"),
    ),
    "ap-southeast": Region(
        code="ap-southeast",
        display_name="Asia Southeast (Singapore)",
        gpu_capacity=24,
        data_residency=("apac", "sg"),
        energy_usd_per_kwh=0.19,
        skus=("t4-16", "a10-24", "l4-24", "a100-80", "h100-80"),
    ),
    "eu-west": Region(
        code="eu-west",
        display_name="Europe West (Dublin)",
        gpu_capacity=32,
        data_residency=("eu", "gdpr"),
        energy_usd_per_kwh=0.21,
        skus=("t4-16", "a10-24", "l4-24", "a100-80", "h100-80"),
    ),
    "eu-central": Region(
        code="eu-central",
        display_name="Europe Central (Frankfurt)",
        gpu_capacity=20,
        data_residency=("eu", "gdpr", "sovereign"),
        energy_usd_per_kwh=0.26,
        skus=("l4-24", "a100-80", "h100-80"),
    ),
    "us-east": Region(
        code="us-east",
        display_name="US East (Virginia)",
        gpu_capacity=48,
        data_residency=("us",),
        energy_usd_per_kwh=0.09,
        skus=("t4-16", "a10-24", "l4-24", "a100-80", "h100-80"),
    ),
    "us-west": Region(
        code="us-west",
        display_name="US West (Oregon)",
        gpu_capacity=40,
        data_residency=("us",),
        energy_usd_per_kwh=0.07,
        skus=("t4-16", "l4-24", "a100-80", "h100-80"),
    ),
}


SKUS: dict[str, Sku] = {
    "t4-16": Sku(code="t4-16", display_name="T4 16GB", vram_gb=16, hourly_usd=0.35),
    "a10-24": Sku(code="a10-24", display_name="A10 24GB", vram_gb=24, hourly_usd=0.90),
    "l4-24": Sku(code="l4-24", display_name="L4 24GB", vram_gb=24, hourly_usd=1.10),
    "a100-80": Sku(code="a100-80", display_name="A100 80GB", vram_gb=80, hourly_usd=8.00),
    "h100-80": Sku(code="h100-80", display_name="H100 80GB", vram_gb=80, hourly_usd=14.50),
}


MODELS: dict[str, Model] = {
    # Open-weight models: no residency requirement, so they may run anywhere.
    "llama-3-8b": Model(
        code="llama-3-8b",
        display_name="Llama 3 8B",
        parameters_b=8.0,
        min_vram_gb=16,
        required_residency=(),
        family="llama",
    ),
    "llama-3-70b": Model(
        code="llama-3-70b",
        display_name="Llama 3 70B",
        parameters_b=70.0,
        min_vram_gb=92,
        required_residency=(),
        family="llama",
    ),
    "mistral-7b": Model(
        code="mistral-7b",
        display_name="Mistral 7B",
        parameters_b=7.0,
        min_vram_gb=16,
        required_residency=(),
        family="mistral",
    ),
    "qwen-14b": Model(
        code="qwen-14b",
        display_name="Qwen 14B",
        parameters_b=14.0,
        min_vram_gb=32,
        required_residency=(),
        family="qwen",
    ),
    "phi-3-mini": Model(
        code="phi-3-mini",
        display_name="Phi-3 Mini",
        parameters_b=3.8,
        min_vram_gb=8,
        required_residency=(),
        family="phi",
    ),
    "whisper-large": Model(
        code="whisper-large",
        display_name="Whisper Large v3",
        parameters_b=1.55,
        min_vram_gb=12,
        required_residency=(),
        family="whisper",
    ),
    # Deliberately narrow: clinical data may only land in sovereign EU regions.
    # This is what makes the residency check worth having at all.
    "med-llm-sovereign": Model(
        code="med-llm-sovereign",
        display_name="Med-LLM (sovereign EU only)",
        parameters_b=13.0,
        min_vram_gb=40,
        required_residency=("eu", "gdpr", "sovereign"),
        family="med",
    ),
    # Deliberately narrow the other way: US data may not leave the US.
    "fin-llm-us": Model(
        code="fin-llm-us",
        display_name="Fin-LLM (US only)",
        parameters_b=20.0,
        min_vram_gb=48,
        required_residency=("us",),
        family="fin",
    ),
}


def get_region(code: str) -> Region | None:
    return REGIONS.get(code)


def get_sku(code: str) -> Sku | None:
    return SKUS.get(code)


def get_model(code: str) -> Model | None:
    return MODELS.get(code)


def regions_for_sku(sku_code: str) -> list[Region]:
    """Every region that sells a given SKU, in stable catalog order."""
    return [r for r in REGIONS.values() if r.offers(sku_code)]


def models_allowed_in(region_code: str) -> list[Model]:
    """Every model whose residency requirements this region satisfies."""
    region = REGIONS.get(region_code)
    if region is None:
        return []
    return [m for m in MODELS.values() if region.permits(m.required_residency)]


def catalog_snapshot() -> dict[str, list[dict[str, object]]]:
    """Serialisable view of the whole catalog, for the regions endpoint."""
    return {
        "regions": [
            {
                "code": r.code,
                "display_name": r.display_name,
                "gpu_capacity": r.gpu_capacity,
                "data_residency": list(r.data_residency),
                "energy_usd_per_kwh": r.energy_usd_per_kwh,
                "skus": list(r.skus),
            }
            for r in REGIONS.values()
        ],
        "skus": [
            {
                "code": s.code,
                "display_name": s.display_name,
                "vram_gb": s.vram_gb,
                "hourly_usd": s.hourly_usd,
            }
            for s in SKUS.values()
        ],
        "models": [
            {
                "code": m.code,
                "display_name": m.display_name,
                "parameters_b": m.parameters_b,
                "min_vram_gb": m.min_vram_gb,
                "required_residency": list(m.required_residency),
                "family": m.family,
            }
            for m in MODELS.values()
        ],
    }
