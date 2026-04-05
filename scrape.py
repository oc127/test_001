import os
import sys

from firecrawl import FirecrawlApp


def main():
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        print("Error: FIRECRAWL_API_KEY environment variable is not set.")
        sys.exit(1)

    app = FirecrawlApp(api_key=api_key)
    result = app.scrape_url("https://example.com", params={"formats": ["markdown"]})
    print(result.get("markdown", "No markdown content returned."))


if __name__ == "__main__":
    main()
