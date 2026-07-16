import sys
sys.path.insert(0, '.')
from app.api.routes import leads_segmented
r = leads_segmented()
for s in r.get('segments', []):
    print(f'{s.get("display_name","?")}: {s.get("count",0)} leads')
    if s.get('leads'):
        first = s['leads'][0]
        print(f'  first: {first.get("business_name","?")} | {first.get("business_email","no email")} | {first.get("status","?")} | source={first.get("source","?")}')
print(f'Total segments: {len(r.get("segments",[]))}')
