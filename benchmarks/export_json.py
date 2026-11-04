import csv, json, pathlib, sys

if len(sys.argv)!=3: raise SystemExit('usage: python export_json.py INPUT_STATS.csv OUTPUT.json')
source=pathlib.Path(sys.argv[1]); target=pathlib.Path(sys.argv[2])
with source.open(newline='',encoding='utf-8') as f: rows=list(csv.DictReader(f))
target.write_text(json.dumps({'source_csv':source.name,'rows':rows},indent=2),encoding='utf-8')
print(target)
