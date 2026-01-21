import re
import time
from functools import reduce

from bs4 import BeautifulSoup

PAGE_URL_SUFFIX = '-pagina-'
HTML_EXTENSION = '.html'

FEATURE_UNIT_DICT = {
    'm²': 'square_meters_area',
    'amb': 'rooms',
    'dorm': 'bedrooms',
    'baño': 'bathrooms',
    'baños': 'bathrooms',
    'coch' : 'parking',
    }

LABEL_DICT = {
    'POSTING_CARD_PRICE' : 'price',
    'expensas' : 'expenses',
    'POSTING_CARD_LOCATION' : 'location',
    'POSTING_CARD_DESCRIPTION' : 'description',
}

class Scraper:
    def __init__(self, browser, base_url):
        self.browser = browser
        self.base_url = base_url

    def scrap_page(self, page_number):
        if page_number == 1:
            page_url = f'{self.base_url}{HTML_EXTENSION}'
        else:
            page_url = f'{self.base_url}{PAGE_URL_SUFFIX}{page_number}{HTML_EXTENSION}'

        print(f'URL: {page_url}')

        page = self.browser.get_text(page_url)

        soup = BeautifulSoup(page, 'lxml')
        estate_posts = soup.find_all('div', attrs = {'data-posting-type' : True})
        estates = []
        for estate_post in estate_posts:
            estate = self.parse_estate(estate_post)
            estates.append(estate)
        return estates

    def scrap_website(self):
        page_number = 1
        estates = []
        estates_scraped = 0
        estates_quantity = self.get_estates_quantity()
        while estates_quantity > estates_scraped:
            print(f'Page: {page_number}')
            estates += self.scrap_page(page_number)
            page_number += 1
            estates_scraped = len(estates)
            time.sleep(3)

        return estates


    def get_estates_quantity(self):
        page_url = f'{self.base_url}{HTML_EXTENSION}'
        page = self.browser.get_text(page_url)
        soup = BeautifulSoup(page, 'lxml')
        soup.find_all('h1')[0].text

        estates_quantity = re.findall(r'\d+\.?\d+', soup.find_all('h1')[0].text)[0]

        estates_quantity = estates_quantity.replace('.', '')

        estates_quantity = int(estates_quantity)
        return estates_quantity

    def parse_estate(self, estate_post):
        # Zonaprop moved many fields from <div> to <h2>/<h3>.
        # Don't restrict to any tag type, just look for data-qa.
        data_qa = estate_post.find_all(attrs={'data-qa': True})
        url = estate_post.get_attribute_list('data-to-posting')[0]
        estate = {}
        estate['url'] = url
        for data in data_qa:
            label = data['data-qa']
            text = None
            if label.startswith('CARD_') or label in {'POSTING_CARD_PUBLISHER'}:
                continue
            if label in ['POSTING_CARD_PRICE', 'expensas']:
                currency_value, currency_type = self.parse_currency_value(data.get_text())
                estate[LABEL_DICT[label] + '_' + 'value'] = currency_value
                estate[LABEL_DICT[label] + '_' + 'type'] = currency_type
            elif label in ['POSTING_CARD_LOCATION', 'POSTING_CARD_DESCRIPTION']:
                text = self.parse_text(data.get_text())
                estate[LABEL_DICT[label]] = text
            elif label in ['POSTING_CARD_FEATURES']:
                features = self.parse_features(data.get_text())
                estate.update(features)
            else:
                text = data.get_text()
                estate[label] = text
        return estate

    def parse_currency_value(self, text):
        try:
            currency_value = re.findall(r'\d+\.?\d+', text)[0]
            currency_value = currency_value.replace('.', '')
            currency_value = int(currency_value)
            currency_type = re.findall(r'(USD)|(ARS)|(\$)', text)[0]
            currency_type = [x for x in currency_type if x != ''][0]
            return currency_value, currency_type
        except:
            return text, None

    def parse_text(self, text):
        text = text.replace('\n', '')
        text = text.replace('\t', '')
        text = text.strip()
        return text

    def parse_features(self, text):

        def normalize_number(raw: str) -> str:
            raw = raw.strip()
            # handle thousand separators and decimal comma
            raw = raw.replace('.', '').replace(',', '.')
            return raw

        def normalize_unit(raw: str) -> str:
            raw = raw.strip().lower()
            raw = raw.rstrip('.')
            # normalize common variants
            if raw in {'m²', 'm2'}:
                return 'm²'
            if raw in {'amb', 'ambs'}:
                return 'amb'
            if raw in {'dorm', 'dorms'}:
                return 'dorm'
            if raw in {'baño', 'baños'}:
                return 'baños'
            if raw in {'coch', 'cocheras'}:
                return 'coch'
            return raw

        def area_kind(qualifier: str | None) -> str | None:
            if not qualifier:
                return None
            q = qualifier.strip().lower().rstrip('.')
            if q.startswith('tot'):
                return 'square_meters_total'
            if q.startswith('cub'):
                return 'square_meters_covered'
            if q.startswith('terr'):
                return 'square_meters_land'
            return None

        # Examples seen in the wild:
        # "771 m² tot. | 8 amb. | 4 dorm. | 3 baños | 1 coch."
        # We parse number + unit + optional qualifier for m².
        pattern = re.compile(
            r'(\d+(?:[\.,]\d+)*)\s*'
            r'(m²|m2|amb\.?|dorm\.?|baños?|baño|coch\.?)(?:\s*(tot\.?|totales|cub\.?|cubiertos|terr\.?|terreno))?',
            flags=re.IGNORECASE,
        )

        features_appearance = {
            'square_meters_area': 0,
            'square_meters_total': 0,
            'square_meters_covered': 0,
            'square_meters_land': 0,
            'rooms': 0,
            'bedrooms': 0,
            'bathrooms': 0,
            'parking': 0,
        }

        features = {}
        for raw_value, raw_unit, raw_qual in pattern.findall(text):
            value = normalize_number(raw_value)
            unit = normalize_unit(raw_unit)

            if unit == 'm²':
                base_key = area_kind(raw_qual) or 'square_meters_area'
            else:
                base_key = FEATURE_UNIT_DICT.get(unit)

            if not base_key:
                # Unknown unit; keep something usable.
                base_key = unit

            idx = features_appearance.get(base_key)
            if idx is None:
                features[base_key] = value
            else:
                features[f'{base_key}_{idx}'] = value
                features_appearance[base_key] += 1

        return features
