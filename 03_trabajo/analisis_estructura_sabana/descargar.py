from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json, urllib.request, sys

BASE = Path(__file__).resolve().parent
items = json.loads((BASE / (sys.argv[1] if len(sys.argv)>1 else 'descargas_temporales.json')).read_text(encoding='utf-8-sig'))
def descargar(item):
    target = BASE / 'inputs' / item['sala'] / item['name']
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == item['size']:
        return True
    try:
        with urllib.request.urlopen(item['download_url'], timeout=45) as response:
            target.write_bytes(response.read())
        return True
    except Exception as exc:
        print(json.dumps({'archivo':item['name'], 'error':str(exc)}), flush=True)
        return False
with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(descargar, items))
print(json.dumps({'descargados':sum(results),'solicitados':len(items)}))
