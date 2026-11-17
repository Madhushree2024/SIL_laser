import pytest
from pydantic import ValidationError
from app.laser_control.cutting_parameters import CuttingParameters, LayerParameters


@pytest.mark.parametrize('values',[{'max_power_percent':101},{'speed_mm_s':0},{'passes':0},{'min_power_percent':80,'max_power_percent':20},{'thickness_mm':float('nan')}])
def test_invalid_parameters(values):
    with pytest.raises(ValidationError): CuttingParameters(**values)


def test_layer_overrides_inherit_and_cross_validate():
    params=CuttingParameters(speed_mm_s=20,max_power_percent=30,passes=3,layers={'CUT':LayerParameters(min_power_percent=40)})
    assert 'minimum power' in params.missing_settings(['CUT'])[0]
    assert LayerParameters().passes is None
    assert CuttingParameters(layers={'CUT':LayerParameters(enabled=False)}).missing_settings(['CUT'])==['Enable at least one cutting layer.']
