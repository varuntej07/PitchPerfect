import csv
import re
import asyncio
from playwright.async_api import async_playwright


class ScrapeCricbuzz:
    def __init__(self, teams):
        """Initialize Playwright variables."""
        self.browser = None
        self.page = None
        self.teams = teams

    async def browse(self):
        """Launches the browser and navigates to the page"""
        async with async_playwright() as p:
            self.browser = await p.chromium.launch(headless=False)  # I want the head, to see what's happening
            self.page = await self.browser.new_page()

            await self.yearSelector()

            await self.browser.close()

    async def yearSelector(self):
        """Loop through each year from 2016 and scraping series links"""
        for year in range(2016, 2025):
            try:
                await self.page.goto(
                    f"https://www.cricbuzz.com/cricket-scorecard-archives/{year}",
                    timeout=60000,
                    wait_until="domcontentloaded"
                )
            except TimeoutError:
                print(f"Timeout while navigating to {year}. So, skipping this year.")
                continue

            await self.page.wait_for_timeout(3000)

            series_links = await self.page.query_selector_all('.cb-srs-lst-itm a')

            if series_links:
                await self.fetchingSeriesLinks(series_links, year)
            else:
                print(f"No series found in {year}")

    async def fetchingSeriesLinks(self, series_links, year):
        """Loop all series on the page; for each, click, scrape matches, return"""
        series_dict = {}
        for link in series_links:
            series_href = await link.get_attribute('href')
            series_text = await link.inner_text()

            if series_href:
                series_dict[series_href] = series_text

            for series_href, series_text in series_dict.items():
                if "tour" in series_text.lower() or "tri-series" in series_text.lower() or "Indian Premier League" in series_text:
                    if any(team.lower() in series_text.lower() for team in self.teams):
                        print(f"Visiting series: {series_text}")

                        full_series_link = "https://www.cricbuzz.com" + series_href
                        await self.page.goto(full_series_link, wait_until="domcontentloaded")
                        await asyncio.sleep(1)

                        # now we're inside a series page
                        await self.fetchingMatches(year)

                        # after finishing the entire match loop, go back
                        await self.page.go_back()
                        await self.page.wait_for_load_state('domcontentloaded')
                        await asyncio.sleep(1)

    async def fetchingMatches(self, year):
        """From the series page, gather all matches and visit them one by one"""
        await self.page.wait_for_selector("div.cb-col-100.cb-col.cb-series-matches.ng-scope")
        await asyncio.sleep(2)

        match_links = await self.page.query_selector_all("div.cb-col-100.cb-col.cb-series-matches.ng-scope a")

        matches_dict = {}       # stores unique matches and it's href
        for match in match_links:
            href = await match.get_attribute("href")
            if not href:
                continue

            text = await match.inner_text()
            if href not in matches_dict:
                matches_dict[href] = text

        for match_href, match_text in matches_dict.items():
            if "practice" in match_text.lower() or "warm-up" in match_text.lower():
                continue

            # Must have at least one of our teams
            if not any(team.lower() in match_text.lower() for team in self.teams):
                print(f"Skipping match: {match_text} as no best teams found!")
                continue

            print(f"Scraping the match between- {match_text}")

            full_match_url = "https://www.cricbuzz.com" + match_href
            await self.page.goto(full_match_url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(2000)
            await asyncio.sleep(2)

            # open commentary tab
            await self.fetchingCommentary(match_text, year)

            # after finishing commentary, go back to series' match-list
            await self.page.go_back()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_timeout(5000)
            # await asyncio.sleep(2)

    async def fetchingCommentary(self, match_text, year):
        """Open Commentary tab, load all commentary, extract."""
        commentary_tab = await self.page.wait_for_selector("text=Commentary", timeout=20000)
        if not commentary_tab:
            print("No 'Commentary' tab found!")
            return

        await commentary_tab.click()
        await self.page.wait_for_load_state("domcontentloaded")
        await self.page.wait_for_timeout(5000)

        # selectors for capturing (before, after) and ball-to-ball commentary
        combined_selector = "p.cb-com-ln.ng-binding.ng-scope.cb-col.cb-col-90, .cb-col.cb-col-100.cb-com-ln"

        # clicking 'Load More Commentary' button in a loop
        while True:
            old_count = len(await self.page.query_selector_all(combined_selector))
            load_more_btn = self.page.locator("text=Load More Commentary")

            # Scroll the button into view and click it
            await load_more_btn.scroll_into_view_if_needed()
            await load_more_btn.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_timeout(5000)

            # Checks if new commentary was there
            new_count = len(await self.page.query_selector_all(combined_selector))
            if new_count == old_count:
                print("No new commentary loaded after clicking, stopping.")
                break

        # Perform infinite scroll in case the site lazy-loads more commentary
        consecutive_no_change = 0
        max_no_change = 3
        while True:
            old_scroll_height = await self.page.evaluate("() => document.body.scrollHeight")
            await self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await self.page.wait_for_timeout(7000)  # waiting for content to load for 7 secs

            new_scroll_height = await self.page.evaluate("() => document.body.scrollHeight")
            if new_scroll_height == old_scroll_height:
                # print("No more content loaded after infinite scroll, stopping!")
                consecutive_no_change += 1
                if consecutive_no_change >= max_no_change:
                    break
            else:
                # resetting if we found more content
                consecutive_no_change = 0

        await self.extractCommentary(year, match_text)

    async def extractCommentary(self, year, match_text):
        """Read all commentary lines from the loaded page and write to CSV."""
        combined_selector = "p.cb-com-ln.ng-binding.ng-scope.cb-col.cb-col-90, .cb-col.cb-col-100.cb-com-ln"
        commentary_lines = await self.page.query_selector_all(combined_selector)
        if not commentary_lines:
            print(f"No commentary lines found for {match_text}")
            return

        csv_filename = f"{year}_commentary.csv"
        with open(csv_filename, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            # Writes header only if the file is empty
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
                    # print(f"Commentary line: {text}")


async def main():
    scraper = ScrapeCricbuzz([
        'India', 'Australia', 'England', 'South Africa', 'Pakistan', 'West Indies',
        'New Zealand', 'Royal Challengers Bengaluru', 'Kolkata Knight Riders',
        'Sunrisers Hyderabad', 'Rajasthan Royals', 'Chennai Super Kings', 'Delhi Capitals',
        'Lucknow Super Giants', 'Gujarat Titans', 'Mumbai Indians', 'Punjab Kings'
    ])
    await scraper.browse()


asyncio.run(main())
