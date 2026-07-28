import json
import os
from collections import defaultdict
from datetime import datetime

# ===== Path Configuration =====
INPUT_RECORDS_FILE = "data/records_3/ops/records_5.jsonl"
OUTPUT_MAPS_FILE = "data/topology/endpoint_maps.json"
# ===========================


def load_records(path):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def main():
    endpoint_maps = defaultdict(lambda: defaultdict(set))

    for r in load_records(INPUT_RECORDS_FILE):
        child = r.get("endpointName")
        if not child:
            continue

        parent = r.get("parent_endpoint_name", "-1")
        if parent in (None, "", "-1"):
            parent_key = "None"
        else:
            parent_key = parent

        start_ms = r.get("startTime")
        if start_ms is None:
            continue

        dt = datetime.fromtimestamp(start_ms / 1000.0)
        minute_str = dt.strftime("%Y-%m-%d %H:%M:00")

        endpoint_maps[parent_key][minute_str].add(child)

    # set -> list
    maps_json = {}
    for parent, by_minute in endpoint_maps.items():
        maps_json[parent] = {}
        for minute_str, children in by_minute.items():
            maps_json[parent][minute_str] = sorted(children)

    os.makedirs(os.path.dirname(OUTPUT_MAPS_FILE), exist_ok=True)
    with open(OUTPUT_MAPS_FILE, "w", encoding="utf-8") as f:
        json.dump(maps_json, f, ensure_ascii=False, indent=2)

    print(f"endpoint_maps.json written to: {OUTPUT_MAPS_FILE}")


if __name__ == "__main__":
    main()
