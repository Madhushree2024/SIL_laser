import pytest

from app.nesting.nesting_engine import NestingEngine
from app.templates.template_geometry import CuttingTemplate
from app.templates.template_manager import TemplateManager


@pytest.mark.parametrize("strategy", ["first_fit", "maximum_pieces", "best_fit", "maximum_utilization", "shared_side"])
@pytest.mark.parametrize("first_size,second_size", [(80, 20), (20, 80)])
def test_user_priority_overrides_automatic_size_sort(strategy, first_size, second_size):
    first = CuttingTemplate("preferred", "Preferred", first_size, first_size, quantity=1, priority=1)
    second = CuttingTemplate("later", "Later", second_size, second_size, quantity=1, priority=2)
    result = NestingEngine().nest([[0, 0], [90, 0], [90, 90], [0, 90]], [second, first], edge_clearance_mm=1, strategy=strategy)
    assert [placement.template_id for placement in result.placements] == ["preferred"]


@pytest.mark.parametrize("strategy", ["first_fit", "shared_side"])
def test_unfittable_priority_moves_on_to_next_size(strategy):
    large = CuttingTemplate("large", "12 inch", 304.8, 304.8, priority=1)
    smaller = CuttingTemplate("smaller", "7 inch", 177.8, 177.8, priority=2)
    result = NestingEngine().nest([[0, 0], [200, 0], [200, 200], [0, 200]], [large, smaller], strategy=strategy)
    assert [placement.template_id for placement in result.placements] == ["smaller"]


def test_priority_survives_reload(tmp_path):
    path = tmp_path / "templates.json"
    manager = TemplateManager(path)
    manager.save([CuttingTemplate("12", "12 inch", 304.8, 304.8, priority=1), CuttingTemplate("7", "7 inch", 177.8, 177.8, priority=2)])
    assert [template.priority for template in TemplateManager(path).list()] == [1, 2]
