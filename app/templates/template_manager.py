from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .template_geometry import CuttingTemplate


INCH = 25.4


class TemplateManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(self.default_templates())

    def default_templates(self) -> list[CuttingTemplate]:
        return [
            CuttingTemplate(
                id=f"{inch}in-square",
                name=f'{inch}" square',
                width_mm=inch * INCH,
                height_mm=inch * INCH,
                shape="square",
                quantity=1,
                allowed_rotations=[0, 90],
            )
            for inch in [3, 4, 5, 6, 7, 8, 9, 10]
        ]

    def list(self) -> list[CuttingTemplate]:
        data = json.loads(self.path.read_text())
        return [CuttingTemplate(**item) for item in data]

    def validate(self, templates: list[CuttingTemplate]) -> None:
        ids = set()
        names = set()
        for template in templates:
            if not template.id or template.id in ids:
                raise ValueError("Template IDs must be non-empty and unique")
            ids.add(template.id)
            name = template.name.strip().casefold()
            if not name or name in names:
                raise ValueError("Template names must be non-empty and unique")
            names.add(name)
            if template.priority is not None and (isinstance(template.priority, bool) or not isinstance(template.priority, int) or template.priority < 1):
                raise ValueError("Template priority must be a positive whole number")
            if not all(math.isfinite(value) and value > 0 for value in (template.width_mm, template.height_mm)):
                raise ValueError("Template dimensions must be positive finite numbers")
            if isinstance(template.quantity, bool) or not isinstance(template.quantity, int) or template.quantity < 0:
                raise ValueError("Template quantity must be a non-negative whole number")
            if not template.allowed_rotations or not all(math.isfinite(value) for value in template.allowed_rotations):
                raise ValueError("Template must have at least one finite rotation")
            if not template.geometry().is_valid:
                raise ValueError("Template geometry is invalid")

    def save(self, templates: list[CuttingTemplate]) -> None:
        self.validate(templates)
        # Replace only after validation and a complete write; preserve the old library on failure.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump([asdict(template) for template in templates], handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def delete(self, template_id: str) -> list[CuttingTemplate]:
        templates = self.list()
        if not template_id or not any(item.id == template_id for item in templates):
            raise ValueError("Selected template no longer exists. Refresh the library.")
        remaining = [item for item in templates if item.id != template_id]
        self.save(remaining)
        return remaining
