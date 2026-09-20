import logging
import os

# Change "product_search" to your module's filename, without .py
# (the file that contains DEMO_SEARCH_QUERY and post_json)
from product_search import post_json, targeted_query

logging.basicConfig(level=logging.INFO)

# If your keys live in a .env file, uncomment these two lines:
# from dotenv import load_dotenv
# load_dotenv()

headers = {"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"]}
q = "2 cucumbers buy online Canada"

# 1. Raw search results, before the allowlist filter
doordash_urls = []
for query in (q, targeted_query(q)):
    raw = post_json("https://api.browserbase.com/v1/search", headers=headers,
                    payload={"query": query, "numResults": 25}, provider="BB")
    print("\nQUERY:", query)
    for r in raw["results"]:
        url = r.get("url") or ""
        print("  ", url)
        if "doordash.com" in url:
            doordash_urls.append(url)

# 2. Fetch the first DoorDash URL and look at what actually comes back
if not doordash_urls:
    print("\nNo doordash.com URLs in either search. The search step is the problem.")
else:
    url = doordash_urls[0]
    print("\nFETCHING:", url)
    data = post_json("https://api.browserbase.com/v1/fetch", headers=headers,
                     payload={"url": url, "format": "markdown", "allowRedirects": False},
                     provider="BB")
    print("STATUS:", data.get("statusCode"))
    print((data.get("content") or "")[:1500])