import csv
import re
import asyncio
from playwright.async_api import async_playwright
import time
import random

from helper import retry
class Picks:
    """ Helper class to filter and pick relevant series based on my criteria."""
    def __init__(self, series_dict):
        self.series_dict = series_dict
        self.selected_series = []

    series_picks = [
        'tour', 'tri-series', 'Indian Premier League', 'ICC Champions Trophy', 'Big Bash League',
        'ICC Cricket World Cup', 'Asia Cup', 'Ashes', 'ICC World Test Championship Final',
        'ICC Mens T20 World Cup'
    ]
    should_not_pick = [
        'Qualifier', 'U19', 'Under 19', 'Women', 'Womens', 'India A', 'England Lions', 'Pakistan A',
        'South Africa A', 'Asian', 'New Zealand A', 'Australia A', 'Sri Lanka A', 'Domestic', 'Postponed',
        'Cancelled', 'XI', 'Unofficial', 'warm-up', 'practice'
    ]

    always_include_series = [
        "world test championship", "t20 world cup", "champions trophy",
        "ashes", "cricket world cup", "indian premier league"
    ]

    async def select_series(self):
        for series_href, series_text in self.series_dict.items():
            series_text = series_text.replace("\xa0", "").strip().lower()
            if any(dontpick.lower() in series_text for dontpick in self.should_not_pick):
                # print(f"Removed : {series_text}")
                continue

            if any(key.lower() in series_text for key in self.always_include_series):
                self.selected_series.append((series_href, series_text))
                continue

            if any(pick.lower() in series_text for pick in self.series_picks):
                self.selected_series.append((series_href, series_text))

        return self.selected_series


async def write_commentary_to_csv(commentary_lines, match_text, year):
    csv_filename = f"{year}_commentary.csv"

    with open(csv_filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if f.tell() == 0:
            writer.writerow(["Match", "Commentary"])

        unique_lines = set()
        for line in commentary_lines:
            raw_text = await line.inner_text()
            raw_text = raw_text.strip()
            text = re.sub(r"\s+", " ", raw_text)  # unify whitespace

            if text not in unique_lines:
                unique_lines.add(text)
                writer.writerow([match_text, text])
        print(f"[INFO] Commentary of  length: {len(unique_lines)} has been successfully extracted from {match_text}")


class ScrapeCricbuzz:
    def __init__(self, teams):
        """Initialize Playwright variables and store teams"""
        self.page = None
        self.browser = None
        self.teams = teams

    async def browse(self):
        """Launches the browser and creates, navigates to the years' page."""
        async with async_playwright() as p:
            self.browser = await p.chromium.launch(headless=False)  # I want head
            self.page = await self.browser.new_page()  # Playwright Page object
            try:
                await self.year_selector()
            finally:
                await self.browser.close()

    @retry(max_attempts=5, delay=random.randint(2, 5))
    async def safe_goto(self, url, **kwargs):
        """custom wrapper - Navigates to a URL, retries on failure w random delays"""
        try:
            return await self.page.goto(url, **kwargs)
        except Exception as e:
            print(f"[Error] Page.goto failed: {e}. Retrying with delay...")
            await asyncio.sleep(random.randint(2, 5))
            raise e

    async def year_selector(self):
        """Loops through each year from 2016 and scraping series links"""
        for year in range(2016, 2025):
            try:
                await self.safe_goto(
                    f"https://www.cricbuzz.com/cricket-scorecard-archives/{year}",
                    timeout=60000,
                    wait_until="domcontentloaded"
                )
            except TimeoutError:
                print(f"Timeout while navigating to {year}. So, skipping this year.")
                continue

            await self.page.wait_for_load_state("domcontentloaded")

            series_links = await self.page.query_selector_all('.cb-srs-lst-itm a')  # Series links from the /archives/year page
            if series_links:
                await self.fetch_series_links(series_links, year)
            else:
                print(f"No series found in {year}")

    async def fetch_series_links(self, series_links, year):
        """Loop all series on the page; for each, click, retrieve link text,
        decide if it's a series we care about, then scrape the matches."""
        series_dict = {}
        for link in series_links:
            try:
                series_href = await link.get_attribute('href')
                series_text = await link.inner_text()
                if series_href:
                    series_dict[series_href] = series_text
            except Exception as e:
                print(f"[Error] extracting series link: {e}")
                continue

        print(f"Found total of series {len(series_dict)} in year {year}")

        # Instantiate Picks and filter the series
        picks = Picks(series_dict)
        selected_series = await picks.select_series()

        for series_href, series_text in selected_series:
            full_series_link = "https://www.cricbuzz.com" + series_href
            print(f"Scraping series {series_text}")
            try:
                await self.safe_goto(full_series_link, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(1)

                # now we're inside a series page
                await self.fetch_matches(year)

                # after finishing the entire match loop, go back
                try:
                    await self.page.go_back()
                    await self.page.wait_for_load_state('domcontentloaded')
                except Exception as e:
                    print(f"[Error] going back from series: {e}")
            except Exception as e:
                print(f"[Error] processing series {series_text}: {e}")

    async def fetch_matches(self, year):
        """Scrapes matches sequentially."""
        try:
            await self.page.wait_for_selector("div.cb-col-100.cb-col.cb-series-matches.ng-scope", timeout=15000)
        except Exception as e:
            print(f"{e}, Timeout while loading matches for year {year}, skipping this page")
            return

        match_links = await self.page.query_selector_all("div.cb-col-100.cb-col.cb-series-matches.ng-scope a")
        if not match_links:
            print(f"No matches found in {year}, Skipping")
            return

        matches_dict = {}
        for match in match_links:
            href = await match.get_attribute("href")
            if not href:
                continue

            text = await match.inner_text()
            if href not in matches_dict:
                matches_dict[href] = text

        skip_matches = ['practice', 'warm-up', 'unofficial', 'XI', 'Invitation']
        for match_href, match_text in matches_dict.items():
            match_text = match_text.replace("\xa0", "").strip()
            if any(skip_word in match_text.lower() for skip_word in skip_matches):
                continue

            await self.scrape_match(year, match_href, match_text)

    async def scrape_match(self, year, match_href, match_text):
        """Scrapes a single match commentary sequentially.
        Navigates to the match, opens commentary tab, extracts it, goes back to matches page"""
        print(f"Starting to scrape {match_text}")
        start = time.time()
        full_match_url = "https://www.cricbuzz.com" + match_href
        try:
            await self.safe_goto(full_match_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"Error while navigating to the match - {e}")
            return

        await self.fetch_commentary(match_text, year)  # open commentary tab

        try:
            await asyncio.sleep(2)
            await self.page.go_back()
            await self.page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[Error] Couldn't go back after scraping {match_text}: {e}")
        end = time.time()
        print(f"Scraped match {match_text} in {end - start:.2f} seconds")

    async def retry_loading_comments(self, combined_selector):
        """Retries loading commentary for up to 3 times"""
        old_count = len(await self.page.query_selector_all(combined_selector))
        for retries in range(3):
            await asyncio.sleep(1)  # Give time for commentary to load
            new_count = len(await self.page.query_selector_all(combined_selector))
            if new_count > old_count:
                return

    async def fetch_commentary(self, match_text, year):
        """Open Commentary tab, load all commentary, extract."""
        try:
            await asyncio.sleep(1)
            commentary_tab = await self.page.wait_for_selector("text=Commentary", timeout=20000)
            if commentary_tab:
                await commentary_tab.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(2)
            else:
                print(f"Could not open commentary tab for {match_text}, skipping")
                return
        except Exception as e:
            print(f"[Error] opening commentary tab for {match_text}: {e}")
            return

        await self._load_all_commentary(match_text)

        await self._scroll_to_end()

        # Finally extract commentary
        await self.extract_commentary(year, match_text)

    async def _load_all_commentary(self, match_text):
        """Repeatedly click 'Load More Commentary' if visible until no new commentary is loaded."""
        # selectors for capturing (before, after) and ball-to-ball commentary
        combined_selector = await self.capture_commentary()

        # clicking 'Load More Commentary' button in a loop
        while True:
            old_count = len(await self.page.query_selector_all(combined_selector))
            try:
                load_more_btn = await self.page.query_selector("text=Load More Commentary")
                if load_more_btn and await load_more_btn.is_visible():
                    await load_more_btn.scroll_into_view_if_needed()
                    await load_more_btn.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(3)

                    # Wait for new content to appear
                    await self.retry_loading_comments(combined_selector)

                    new_count = len(await self.page.query_selector_all(combined_selector))
                    if new_count == old_count:
                        print(f"No new commentary loaded after clicking, stopping.")
                        break
                else:
                    print(f"No 'Load More Commentary' button visible yet for {match_text}. Extracting whatever!")
                    break
            except Exception as e:
                raise e

    async def _scroll_to_end(self):
        """Scroll to the bottom of the page until no further
        change in scroll height occurs multiple times."""
        consecutive_no_change = 0
        max_no_change = 3
        while True:
            old_scroll_height = await self.page.evaluate("() => document.body.scrollHeight")
            await self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

            new_scroll_height = await self.page.evaluate("() => document.body.scrollHeight")
            if new_scroll_height == old_scroll_height:
                consecutive_no_change += 1
                if consecutive_no_change >= max_no_change:
                    break
            else:
                consecutive_no_change = 0

    async def capture_commentary(self):
        combined_selector = "p.cb-com-ln.ng-binding.ng-scope.cb-col.cb-col-90, .cb-col.cb-col-100.cb-com-ln"
        # Retry finding 'Load More Commentary' button up to 3 times
        for attempt in range(3):
            load_more_btn = await self.page.query_selector("text=Load More Commentary")
            if load_more_btn and await load_more_btn.is_visible():
                await load_more_btn.scroll_into_view_if_needed()
                await load_more_btn.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(2)

                await self.retry_loading_comments(combined_selector)
            else:
                break
        return combined_selector

    async def extract_commentary(self, year, match_text):
        """Read all commentary lines from the loaded page and write to CSV."""
        combined_selector = "p.cb-com-ln.ng-binding.ng-scope.cb-col.cb-col-90, .cb-col.cb-col-100.cb-com-ln"

        try:
            await asyncio.sleep(1)
            commentary_lines = await self.page.query_selector_all(combined_selector)
            if not commentary_lines:
                print(f"No commentary found for {match_text}, WTF!")
                return

            await write_commentary_to_csv(commentary_lines, match_text, year)
        except Exception as e:
            print(f"[ERROR] Extracting commentary for {match_text}: {e}")

async def main():
    scraper = ScrapeCricbuzz([
        'India', 'Australia', 'England', 'South Africa', 'Pakistan',
        'New Zealand', 'Royal Challengers Bengaluru', 'Kolkata Knight Riders',
        'Sunrisers Hyderabad', 'Rajasthan Royals', 'Chennai Super Kings', 'Delhi Capitals',
        'Lucknow Super Giants', 'Gujarat Titans', 'Mumbai Indians', 'Punjab Kings'
    ])
    await scraper.browse()


asyncio.run(main())
