"""EX42 moet elke zelfstandige zoekvraag precies één keer aanbieden."""

from unittest.mock import Mock

import pytest

from e2e.integral.scenarios import EX42_prowlarr_lus as scenario


@pytest.mark.parametrize(("aantal", "verwacht", "toegestaan"), [(0, 0, True), (1, 1, True), (2, 1, False)])
def test_terugoproepen_uit_een_vraag(monkeypatch, aantal, verwacht, toegestaan):
    stand = {"zoekopdrachten": 5, "terugoproepen": 5, "max_diepte": 1, "mislukte_terugoproepen": 0}
    na = dict(stand, zoekopdrachten=5 + aantal, terugoproepen=5 + aantal)
    monkeypatch.setattr(scenario, "aantallen", Mock(side_effect=[stand, na]))
    aanvraag = Mock()
    if toegestaan:
        antwoord, delta = scenario.meet_zoekvraag(Mock(), aanvraag, verwacht)
        assert antwoord == aanvraag.return_value
        assert delta == {"zoekopdrachten": aantal, "terugoproepen": aantal}
    else:
        with pytest.raises(AssertionError, match="één zoekvraag"):
            scenario.meet_zoekvraag(Mock(), aanvraag, verwacht)
    aanvraag.assert_called_once_with()
