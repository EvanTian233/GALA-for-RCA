from collections import defaultdict
from datetime import datetime
import json
import tqdm

# Store per-minute info for each endpoint
# Structure: {endpoint: {minute: {'calls': int, 'errors': int, 'total_time': int, 'timeout': int}}}
endpoint_stats = defaultdict(lambda: defaultdict(lambda: {'calls': 0, 'errors': 0, 'total_time': 0, 'timeout': 0}))

# File path
file_path = 'data/records_3/ops/records_6.jsonl'

# def preetify_endpoint_Name(s):
#     splits = s.split('/')
#     splits = [s for s in splits if ' ' not in s and '-' not in s]
#     return '/'.join(splits)

# Process file
with open(file_path, 'r') as file:
    for line in tqdm.tqdm(file):
        data = json.loads(line)
        # Extract required info
        service_code = data['serviceCode']
        endpoint = data['endpointName']
        start_time = data['startTime']
        end_time = data['endTime']
        is_error = data['isError']
        is_timeout = data['timeout']

        
        # Convert timestamp to minute
        minute = datetime.fromtimestamp(start_time // 1000).strftime('%Y-%m-%d %H:%M:00')
        
        # Update statistics
        stats = endpoint_stats[endpoint][minute]
        stats['calls'] += 1
        stats['total_time'] += end_time - start_time
        if is_error:
            stats['errors'] += 1
        if is_timeout:
            stats['timeout'] += 1


# Aggregate data and compute success rate, error rate, and average duration
aggregated_stats = {}
for endpoint, minute_data in tqdm.tqdm(endpoint_stats.items()):
    for minute, stats in minute_data.items():
        success_rate = (1 - stats['errors'] / stats['calls']) * 100 if stats['calls'] > 0 else 0
        error_rate = (stats['errors'] / stats['calls']) * 100 if stats['calls'] > 0 else 0
        average_duration = stats['total_time'] / stats['calls'] if stats['calls'] > 0 else 0
        timeout_rate = (stats['timeout'] / stats['calls']) * 100 if stats['calls'] > 0 else 0
        aggregated_stats.setdefault(endpoint, {})[minute] = {
            'calls': stats['calls'],
            'success_rate': success_rate,
            'error_rate': error_rate,
            'average_duration': average_duration,
            'timeout_rate': timeout_rate
        }
        # if timeout_rate != 0.0:
        #     print(f"endpoint: {endpoint}, minute: {minute}, timeout_rate: {timeout_rate}")

# Write results to file
import os
path = "data/metric/"
if os.path.exists(path) == False:
    os.makedirs(path)
output_file = path + 'endpoint_stats.json'
with open(output_file, 'w') as f:
    json.dump(aggregated_stats, f, indent=4)
