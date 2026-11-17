import pytest

from app.templates.template_geometry import CuttingTemplate
from app.templates.template_manager import TemplateManager
from app.nesting.nesting_engine import NestingEngine


def custom(**changes):
    values = dict(id="custom-serving", name="Serving plate", width_mm=63.5, height_mm=50.8, shape="rectangle", quantity=2, allowed_rotations=[0, 90])
    values.update(changes)
    return CuttingTemplate(**values)


def test_preferred_sizes_persist_with_existing_library(tmp_path):
    path = tmp_path / "templates.json"
    manager = TemplateManager(path)
    initial = manager.list()
    manager.save([*initial, custom()])
    restored = TemplateManager(path).list()
    assert [template.id for template in restored[:-1]] == [template.id for template in initial]
    assert restored[-1].width_mm == 63.5
    assert restored[-1].height_mm == 50.8
    result = NestingEngine().nest([[0, 0], [200, 0], [200, 180], [0, 180]], [restored[-1]], spacing_mm=2, edge_clearance_mm=2, strategy="first_fit")
    assert len(result.placements) == 2


@pytest.mark.parametrize("changes", [{"width_mm": 0}, {"height_mm": -2}, {"quantity": -1}, {"width_mm": float("nan")}, {"allowed_rotations": []}])
def test_invalid_preference_preserves_saved_library(tmp_path, changes):
    path = tmp_path / "templates.json"
    manager = TemplateManager(path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        manager.save([custom(**changes)])
    assert path.read_bytes() == original


def test_duplicate_ids_rejected(tmp_path):
    manager = TemplateManager(tmp_path / "templates.json")
    with pytest.raises(ValueError, match="unique"):
        manager.save([custom(), custom()])
