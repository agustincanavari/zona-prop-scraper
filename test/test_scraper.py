import pytest_mock
from src.scraper import Scraper


class TestScraper():

    def test_scraper(self, mocker: pytest_mock.MockFixture, html_page: str):
        browser = mocker.patch('src.browser.Browser')
        scraper = Scraper(browser, 'fake_url.com')
        scraper.get_estates_quantity = mocker.MagicMock(return_value=20)
        browser.get_text = mocker.MagicMock(return_value=html_page)
        estates = scraper.scrap_website()
        assert len(estates) == 20


def test_parse_features_parses_square_meters_and_abbreviations():
    scraper = Scraper(browser=None, base_url='fake')
    text = '771 m² tot. | 8 amb. | 4 dorm. | 3 baños | 1 coch.'
    features = scraper.parse_features(text)

    assert features['square_meters_total_0'] == '771'
    assert features['rooms_0'] == '8'
    assert features['bedrooms_0'] == '4'
    assert features['bathrooms_0'] == '3'
    assert features['parking_0'] == '1'
