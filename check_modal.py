import urllib.request
import re

req = urllib.request.Request("https://recalldreams.dev/2026/04/24/hello-world/")
req.add_header("User-Agent", "Mozilla/5.0")
resp = urllib.request.urlopen(req, timeout=15)
html = resp.read().decode()

if "jetpack-subscription-modal" in html:
    print("Modal found in HTML")
    idx = html.find("jetpack-subscription-modal")
    # Print context around it
    start = max(0, idx - 200)
    end = min(len(html), idx + 1000)
    print(html[start:end])
else:
    print("NO modal found in HTML")
    # Check for coming-soon
    if "wpcom-coming-soon" in html:
        print("Coming-soon page detected")
    print("Last 2000 chars:")
    print(html[-2000:])
