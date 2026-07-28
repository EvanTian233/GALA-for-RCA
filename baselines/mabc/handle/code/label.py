import json
import time
import tqdm

# Convert records_4 format to records_6 format using DFS
def _dfs(records_4, span, parent_endpoint_name=None):
    if "parent_endpoint_name":
        span["parent_endpoint_name"] = parent_endpoint_name
    span["endpointName"] = span["serviceCode"] + "-" + span["endpointName"]
    children = span.pop("children")
    records_4.append(span)
    for child in children:
        records_4 = _dfs(records_4, child, span["endpointName"])
    return records_4

record_4_path = "data/records_3/ops/records_4.jsonl"
records_4 = []
with open(record_4_path, "r", encoding="utf-8") as records_4_file:
    for line in tqdm.tqdm(records_4_file):
        span = json.loads(line)
        records_4 = _dfs(records_4, span)

# Write to file
with open("data/records_3/ops/records_6.jsonl", "w", encoding="utf-8") as output:
    for span in records_4:
        output.write(f"{json.dumps(span, ensure_ascii=False)}\n")


record_6_path = "data/records_3/ops/records_6.jsonl"

records_6 = []
with open(record_6_path, "r", encoding="utf-8") as records_6_file:
    for line in tqdm.tqdm(records_6_file):
        span = json.loads(line)
        records_6.append(span)

# Find all spans with timeout=true within the same traceId
# Group by traceId
trace_ids = []
trace_id_to_spans = {}
for span in tqdm.tqdm(records_6):
    trace_id = span["traceId"]
    trace_ids.append(trace_id)

    if trace_id not in trace_id_to_spans:
        trace_id_to_spans[trace_id] = []
    trace_id_to_spans[trace_id].append(span)

trace_ids = list(set(trace_ids))
print("trace_id length: ", len(trace_ids))


# Data analysis (optional, not part of main flow)
# startTime => minute_label
# endpointName
minute_labels = set()
endpointNames = set()
for span in tqdm.tqdm(records_6):
    minute_labels.add(time.strftime("%Y-%m-%d %H:%M:00", time.localtime(span["startTime"] / 1000)))
    endpointNames.add(span["endpointName"])
print("minute_labels length: ", len(minute_labels))
print("endpointNames length: ", len(endpointNames))

# Find all spans with timeout=true
trace_id_timeout_spans = {}
for trace_id in tqdm.tqdm(trace_ids):
    for span in trace_id_to_spans[trace_id]:
        if trace_id not in trace_id_timeout_spans:
            trace_id_timeout_spans[trace_id] = []
        else:
            if span["timeout"]:
                trace_id_timeout_spans[trace_id].append(span)


i = 0
# Write to file
with open("data/records_3/ops/trace_id_timeout_spans.jsonl", "w", encoding="utf-8") as output:
    for trace_id, timeout_spans in tqdm.tqdm(trace_id_timeout_spans.items()):
        # print(f"{i}th")
        # i += 1
        if len(timeout_spans) == 0:
            continue
        output.write(f"{json.dumps({'traceId': trace_id, 'timeoutSpans': timeout_spans}, ensure_ascii=False)}\n")

self_reason = 0
other_reason = 0
# Build timeout_path, first_starttime, last_starttime from trace_id_timeout_spans
timeout_path = {}
for trace_id, timeout_spans in tqdm.tqdm(trace_id_timeout_spans.items()):
    if len(timeout_spans) == 0:
        continue
    elif len(timeout_spans) == 1:
        self_reason += 1
    else:
        other_reason += 1
    def _timestamp2timestr(timestamp):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp / 1000))
    timeout_path[trace_id] = {
        "timeout_path": [],
        "first_starttime": timeout_spans[0]["startTime"],
        "last_starttime": timeout_spans[-1]["startTime"],
        "first_starttime_": _timestamp2timestr(timeout_spans[0]["startTime"]),
        "last_starttime_": _timestamp2timestr(timeout_spans[-1]["startTime"]),
        # minute_label seconds should be 00
        "minitus_label": time.strftime("%Y-%m-%d %H:%M:00", time.localtime(timeout_spans[0]["startTime"] / 1000))
    }
    for span in timeout_spans:
        timeout_path[trace_id]['timeout_path'].append(span["endpointName"])

# Write to file
with open("data/records_3/ops/timeout_path.jsonl", "w", encoding="utf-8") as output:
    for trace_id, timeout_path_info in timeout_path.items():
        output.write(f"{json.dumps({'traceId': trace_id, 'timeoutPathInfo': timeout_path_info}, ensure_ascii=False)}\n")

print(f"self_reason: {self_reason}")
print(f"other_reason: {other_reason}")

# Write to file sorted by first_starttime
timeout_path_sorted = sorted(timeout_path.items(), key=lambda x: x[1]["first_starttime"])
with open("data/records_3/ops/timeout_path_sorted.jsonl", "w", encoding="utf-8") as output:
    for trace_id, timeout_path_info in timeout_path_sorted:
        output.write(f"{json.dumps({'traceId': trace_id, 'timeoutPathInfo': timeout_path_info}, ensure_ascii=False)}\n")


# Use timeout_path[0] and minute_label as key, traceId+timeoutPathInfo as value
c_dict = {}
for trace_id, timeout_path_info in tqdm.tqdm(timeout_path.items()):
    # key = timeout_path_info['timeout_path'][0]
    # key_2 = timeout_path_info['minitus_label']
    key = timeout_path_info['minitus_label']
    key_2 = timeout_path_info['timeout_path'][0]
    
    if key not in c_dict:
        c_dict[key] = {}
    if key_2 not in c_dict[key]:
        c_dict[key][key_2] = []
    c_dict[key][key_2].append(
        timeout_path_info['timeout_path']
        # {
        #     "traceId": trace_id,
        #     "timeout_path": timeout_path_info['timeout_path'],
        #     "first_starttime": timeout_path_info["first_starttime"],
        #     "last_starttime": timeout_path_info["last_starttime"],
        #     "first_starttime_": timeout_path_info["first_starttime_"],
        #     "last_starttime_": timeout_path_info["last_starttime_"],
        #     "minitus_label": timeout_path_info["minitus_label"]
        # }
    )

# Deduplicate
for key, value in tqdm.tqdm(c_dict.items()):
    for key_2, value_2 in value.items():
        new_v = list(set(tuple(i) for i in value_2))
        c_dict[key][key_2] = []
        for v in new_v:
            c_dict[key][key_2].append(list(v))

with open("data/records_3/ops/c_dict.json", "w", encoding="utf-8") as output:
    json.dump(c_dict, output, ensure_ascii=False, indent=4)

# Sort by key (time)
from collections import OrderedDict
sorted_c_dict = OrderedDict(sorted(c_dict.items()))

# Write to file
with open("data/records_3/ops/label.json", "w", encoding="utf-8") as output:
    json.dump(sorted_c_dict, output, ensure_ascii=False, indent=4)


# Data analysis (optional, not part of main flow)
minute_labels = set()
endpointNames = set()
for k, v in tqdm.tqdm(c_dict.items()):
    minute_labels.add(k)
    for kk, vv in v.items():
        endpointNames.add(kk)
print("minute_labels length: ", len(minute_labels))
print("endpointNames length: ", len(endpointNames))