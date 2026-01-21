import argparse
import json
import re
import time
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import pandas as pd
from bs4 import BeautifulSoup

from src.browser import Browser
from src.scraper import Scraper
from src import utils


ZONAPROP_HOST = "https://www.zonaprop.com.ar"


def _clean_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # keep only the first numeric token
    m = re.search(r"\d+(?:[\.,]\d+)*", s)
    if not m:
        return None
    s = m.group(0)
    # 1.234,56 -> 1234.56 ; 1.234 -> 1234
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_url(url: str) -> str:
    url = unescape(url).strip()
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        url = urljoin(ZONAPROP_HOST, url)
    if not url.startswith("http"):
        url = urljoin(ZONAPROP_HOST + "/", url)

    # strip tracking query params but keep canonical path
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _extract_jsonld(soup: BeautifulSoup) -> list[Any]:
    blocks: list[Any] = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        txt = s.get_text(strip=True)
        if not txt:
            continue
        try:
            blocks.append(json.loads(txt))
        except Exception:
            continue
    return blocks


def _walk_json(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_json(v)


def _parse_detail_areas_from_jsonld(blocks: list[Any]) -> dict[str, float | None]:
    # Best-effort mapping.
    out: dict[str, float | None] = {
        "m2_total": None,
        "m2_covered": None,
        "m2_land": None,
    }

    for d in _walk_json(blocks):
        # Common: offers.price / offers.priceCurrency
        # Areas: floorSize / lotSize / area as QuantitativeValue
        for key, target in (
            ("floorSize", "m2_covered"),
            ("lotSize", "m2_land"),
            ("area", "m2_total"),
        ):
            if key not in d or out[target] is not None:
                continue
            v = d.get(key)
            if isinstance(v, dict):
                num = v.get("value") or v.get("@value")
                out[target] = _clean_number(num)
            elif isinstance(v, (int, float, str)):
                out[target] = _clean_number(v)

        # Sometimes in additionalProperty: [{name,value}]
        ap = d.get("additionalProperty")
        if isinstance(ap, list):
            for item in ap:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip().lower()
                val = item.get("value")
                if out["m2_total"] is None and "superficie" in name and "total" in name:
                    out["m2_total"] = _clean_number(val)
                if out["m2_covered"] is None and ("cubierta" in name or "cubiertos" in name):
                    out["m2_covered"] = _clean_number(val)
                if out["m2_land"] is None and ("terreno" in name or "lote" in name):
                    out["m2_land"] = _clean_number(val)

    return out


def _parse_detail_areas_from_html(soup: BeautifulSoup) -> dict[str, float | None]:
    # Generic fallback: scan text blocks for known labels and pick the first number + m².
    out: dict[str, float | None] = {
        "m2_total": None,
        "m2_covered": None,
        "m2_land": None,
    }

    label_map = {
        "superficie total": "m2_total",
        "superficie cubierta": "m2_covered",
        "superficie del terreno": "m2_land",
        "superficie terreno": "m2_land",
        "terreno": "m2_land",
        "lote": "m2_land",
    }

    # Build a list of candidate elements with compact text
    candidates = soup.find_all(["li", "div", "section"], limit=2000)
    for el in candidates:
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()

        for lbl, key in label_map.items():
            if out[key] is not None:
                continue
            if lbl in low:
                # capture patterns like "219 m²" or "219m2"
                import re

                m = re.search(r"(\d+(?:[\.,]\d+)*)\s*(m²|m2)", txt, flags=re.IGNORECASE)
                if m:
                    out[key] = _clean_number(m.group(1))

        if out["m2_total"] is not None and out["m2_covered"] is not None and out["m2_land"] is not None:
            break

    return out


def _parse_detail_areas_from_icon_features(soup: BeautifulSoup) -> dict[str, float | None]:
    # Zonaprop detail pages often show areas as icon-feature list items:
    # - <i class="icon-stotal"></i> 771 m² tot.
    # - <i class="icon-scubierta"></i> 267 m² cub.
    # - <i class="icon-sterreno"></i> ...
    out: dict[str, float | None] = {
        "m2_total": None,
        "m2_covered": None,
        "m2_land": None,
    }

    for li in soup.select("li.icon-feature"):
        icon = li.select_one("i")
        if not icon:
            continue
        classes = set(icon.get("class") or [])
        txt = li.get_text(" ", strip=True)
        val = _clean_number(txt)
        if val is None:
            continue

        if "icon-scubierta" in classes:
            out["m2_covered"] = out["m2_covered"] or val
        elif "icon-stotal" in classes:
            out["m2_total"] = out["m2_total"] or val
        elif "icon-sterreno" in classes:
            out["m2_land"] = out["m2_land"] or val

        if out["m2_total"] is not None and out["m2_covered"] is not None and out["m2_land"] is not None:
            break

    return out


def parse_listing_detail(browser: Browser, url: str, sleep_s: float = 0.0) -> dict[str, Any]:
    url = _normalize_url(url)
    html = browser.get_text(url)
    soup = BeautifulSoup(html, "lxml")

    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    blocks = _extract_jsonld(soup)
    # Prefer HTML icon-features (most reliable), then labeled HTML, then JSON-LD.
    areas_jsonld = _parse_detail_areas_from_jsonld(blocks)
    areas_icons = _parse_detail_areas_from_icon_features(soup)
    areas_labels = _parse_detail_areas_from_html(soup)
    areas = {
        "m2_total": areas_icons.get("m2_total") or areas_labels.get("m2_total") or areas_jsonld.get("m2_total"),
        "m2_covered": areas_icons.get("m2_covered") or areas_labels.get("m2_covered") or areas_jsonld.get("m2_covered"),
        "m2_land": areas_icons.get("m2_land") or areas_labels.get("m2_land") or areas_jsonld.get("m2_land"),
    }

    if sleep_s:
        time.sleep(sleep_s)

    return {
        "link": url,
        "title": title,
        **areas,
    }


def export_search_to_excel(
    search_url: str,
    output: str | None,
    max_listings: int | None,
    sleep_detail_s: float,
) -> str:
    base_url = utils.parse_zonaprop_url(search_url)

    browser = Browser()
    scraper = Scraper(browser, base_url)
    cards = scraper.scrap_website()

    # Normalize card urls and optionally limit
    for c in cards:
        if "url" in c:
            c["link"] = _normalize_url(c["url"])

    if max_listings is not None:
        cards = cards[: max_listings]

    rows: list[dict[str, Any]] = []
    for i, card in enumerate(cards, start=1):
        link = card.get("link") or card.get("url")
        if not link:
            continue

        try:
            detail = parse_listing_detail(browser, str(link), sleep_s=sleep_detail_s)
        except Exception as e:
            detail = {"link": _normalize_url(str(link)), "detail_error": f"{type(e).__name__}: {e}"}

        # Best-effort: bring card features into friendly columns
        m2_total = detail.get("m2_total") or _clean_number(card.get("square_meters_total_0"))
        m2_covered = detail.get("m2_covered")
        price_value = card.get("price_value")
        try:
            price_value_num = float(price_value) if price_value is not None else None
        except (TypeError, ValueError):
            price_value_num = None

        precio_por_m2 = None
        denom = m2_covered or m2_total
        if price_value_num is not None and denom is not None and denom > 0:
            precio_por_m2 = price_value_num / denom

        merged = {
            "link": detail.get("link"),
            "title": detail.get("title"),
            "location": card.get("location"),
            "price_value": price_value,
            "price_type": card.get("price_type"),
            "expenses_value": card.get("expenses_value"),
            "expenses_type": card.get("expenses_type"),
            "m2_total": m2_total,
            "m2_covered": m2_covered,
            "m2_land": detail.get("m2_land"),
            "precio_por_m2": precio_por_m2,
            "rooms": _clean_number(card.get("rooms_0")),
            "bedrooms": _clean_number(card.get("bedrooms_0")),
            "bathrooms": _clean_number(card.get("bathrooms_0")),
            "parking": _clean_number(card.get("parking_0")),
            "description": card.get("description"),
            "detail_error": detail.get("detail_error"),
        }

        print(f"[{i}/{len(cards)}] {merged['link']}")
        rows.append(merged)

    df = pd.DataFrame(rows)

    if output is None:
        output = utils.get_filename_from_datetime(base_url, "xlsx")

    utils.create_root_directory(output)
    df.to_excel(output, index=False)
    return output


def main():
    parser = argparse.ArgumentParser(description="Export Zonaprop search results to Excel")
    parser.add_argument("url", help="Zonaprop search URL (e.g. https://www.zonaprop.com.ar/casas-venta-talar-del-lago-i.html)")
    parser.add_argument("--output", "-o", default=None, help="Output .xlsx path (default: data/<...>.xlsx)")
    parser.add_argument("--max", type=int, default=None, help="Max listings to scrape (useful for quick tests)")
    parser.add_argument("--sleep-detail", type=float, default=1.0, help="Seconds to sleep between detail pages")
    args = parser.parse_args()

    out = export_search_to_excel(
        search_url=args.url,
        output=args.output,
        max_listings=args.max,
        sleep_detail_s=args.sleep_detail,
    )
    print(f"Excel saved to {out}")


if __name__ == "__main__":
    main()
