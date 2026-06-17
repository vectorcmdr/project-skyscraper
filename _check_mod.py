import json, urllib.request
d = json.loads(urllib.request.urlopen('https://project-skyscraper.vectorcmdr.xyz/data/feed.json').read())
mod = [e for e in d['entries'] if e.get('type') == 'api_items_modified']
mod.sort(key=lambda x: x.get('timestamp',''), reverse=True)
print(f'{len(mod)} api_items_modified entries')
for e in mod[:5]:
    print(f'  ts={e.get("timestamp","")[:19]} title={e.get("title","")[:40]}')
