from pydantic import BaseModel, ConfigDict, Field, model_validator


class LayerParameters(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: bool = True
    speed_mm_s: float | None = Field(default=None, gt=0, le=10000)
    max_power_percent: float | None = Field(default=None, ge=0, le=100)
    min_power_percent: float | None = Field(default=None, ge=0, le=100)
    passes: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode='after')
    def powers(self):
        if self.min_power_percent is not None and self.max_power_percent is not None and self.min_power_percent > self.max_power_percent:
            raise ValueError('Minimum power must not exceed maximum power.')
        return self


class CuttingParameters(LayerParameters):
    passes: int = Field(default=1, ge=1, le=100)
    material: str = Field(default='Areca sheath', min_length=1, max_length=120)
    thickness_mm: float = Field(default=2.5, gt=0, le=100)
    layers: dict[str, LayerParameters] = Field(default_factory=dict, max_length=100)

    def missing_settings(self, layer_names: list[str]) -> list[str]:
        errors = []
        enabled = 0
        for name in layer_names:
            layer = self.layers.get(name)
            if layer is not None and not layer.enabled:
                continue
            enabled += 1
            values = self.model_dump(exclude={'layers'})
            if layer:
                values.update(layer.model_dump(exclude_none=True))
            if values['speed_mm_s'] is None or values['max_power_percent'] is None:
                errors.append(f'Layer {name}: enter a tested speed and maximum power.')
            lo, hi = values['min_power_percent'], values['max_power_percent']
            if lo is not None and hi is not None and lo > hi:
                errors.append(f'Layer {name}: minimum power exceeds maximum power.')
        if not enabled:
            errors.append('Enable at least one cutting layer.')
        return errors
