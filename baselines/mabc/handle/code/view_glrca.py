#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
view_new.py

Convert Jaeger-style trace CSV to the format required by MABC / view.py,
and execute the original view.py root-cause preprocessing pipeline:

records -> records_2 -> deduplicate -> records_5 -> filter non-root-cause -> records_3
Also generates scatter plots and histograms for each endpoint.

Usage:
    python view_new.py
"""

import csv
import json
import logging
import math
import os
import copy
import shutil

import tqdm
import matplotlib

# Non-interactive backend for server-side execution
matplotlib.use('Agg')
from matplotlib import pyplot as plt

# ========= Configuration Section ==============
# Your trace CSV path (modify to your own file)
INPUT_CSV = 'data/traces.csv'

# Output directories (records_5 / records_3 / figures)
OUTPUT_RECORDS_DIR = 'data/records_3/ops'
OUTPUT_FIG_DIR = 'data/records_3/opsfigs'
RECORDS_5_PATH = os.path.join(OUTPUT_RECORDS_DIR, 'records_5.jsonl')
RECORDS_3_PATH = os.path.join(OUTPUT_RECORDS_DIR, 'records_3.jsonl')
# ==========================================

logging.basicConfig(
    level=getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper()),
    format='[%(asctime)s] %(levelname)s - %(message)s'
)


def preetify_endpoint_Name(s):
    """
    Normalize endpointName: currently just strips whitespace.
    Modify this function for additional cleaning (e.g., parameter normalization).
    """
    if s is None:
        return 'unknown'
    if not isinstance(s, str):
        s = str(s)
    return s.strip()


def load_records_from_csv(csv_path):
    """
    Load trace from CSV and convert to record structure expected by view.py:

    {
        "traceId": ...,
        "segmentId": ...,
        "spanId": ...,
        "parentSpanId": ...,
        "endpointName": ...,
        "startTime": ... (milliseconds),
        "endTime": ...   (milliseconds),
        "statusCode": ...,
        "refs": []       (no cross-segment info, empty list)
    }
    """
    records = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            trace_id = (row.get('traceID') or '').strip()
            span_id = (row.get('spanID') or '').strip()
            if not trace_id or not span_id:
                # Skip rows without traceID / spanID
                continue

            # ---- Parent span handling: empty means root span ----
            parent_span_id = (row.get('parentSpanID') or '').strip()
            if parent_span_id == '' or parent_span_id.lower() in ('null', 'none'):
                parent_span_id = '-1'

            # ---- Time handling: startTimeMillis + duration(microseconds) -> start/end milliseconds ----
            start_ms_raw = (row.get('startTimeMillis') or row.get('startTime') or '').strip()
            if start_ms_raw == '':
                continue
            try:
                start_ms = int(float(start_ms_raw))
            except ValueError:
                # Skip abnormal values
                continue

            duration_raw = (row.get('duration') or '0').strip()
            try:
                duration_us = float(duration_raw)
            except ValueError:
                duration_us = 0.0
            duration_ms = duration_us / 1000.0  # microseconds -> milliseconds
            end_ms = start_ms + duration_ms

            # ---- endpointName selection strategy ----
            service_name = (row.get('serviceName') or '').strip()
            method_name = (row.get('methodName') or '').strip()
            operation_name = (row.get('operationName') or '').strip()

            if service_name and method_name:
                # Normal case: serviceName/methodName
                endpoint_name = f'{service_name}/{method_name}'
            elif service_name:
                # When methodName is empty, use only serviceName to avoid "xxxservice/"
                endpoint_name = service_name
            elif operation_name:
                # If serviceName is also empty, fall back to operationName
                endpoint_name = operation_name
            else:
                endpoint_name = 'unknown'

            endpoint_name = preetify_endpoint_Name(endpoint_name)

            # ---- statusCode handling: treat empty as 0 ----
            status_raw = (row.get('statusCode') or '').strip()
            if status_raw == '' or status_raw.lower() in ('null', 'none'):
                status_code = 0
            else:
                try:
                    status_code = int(float(status_raw))
                except ValueError:
                    status_code = 0

            record = {
                'traceId': trace_id,
                'segmentId': trace_id,          # No segmentId, use traceId as substitute
                'spanId': span_id,
                'parentSpanId': parent_span_id,
                'endpointName': endpoint_name,
                'startTime': start_ms,
                'endTime': end_ms,
                'statusCode': status_code,
                'refs': [],                    # No cross-segment info
            }
            records.append(record)

    return records


def main():
    logging.info('Loading traces from %s', INPUT_CSV)
    records = load_records_from_csv(INPUT_CSV)
    logging.info('Loaded %d spans', len(records))
    if not records:
        logging.error('No records loaded, aborting.')
        return

    # Time base for plotting
    min_time = min(r['startTime'] for r in records)

    # ========= Build segment-span -> endpointName mapping =========
    segment_id_span_to_endpoint_name = {}
    for record in records:
        key = f"{record['segmentId']}-{record['spanId']}"
        segment_id_span_to_endpoint_name[key] = preetify_endpoint_Name(record['endpointName'])

    # ========= records -> records_2: add new_* fields, parent endpoint =========
    stats = {}
    records_2 = []
    for record in tqdm.tqdm(records, desc='build records_2'):
        record_2 = dict(record)
        record_2['new_span_id'] = f"{record['segmentId']}-{record['spanId']}"
        record_2['new_parent_span_id'] = f"{record['segmentId']}-{record['parentSpanId']}"
        record_2['duration'] = record['endTime'] - record['startTime']
        record_2['endpointName'] = preetify_endpoint_Name(record['endpointName'])

        new_parent_span_id = record_2['new_parent_span_id']
        if '--1' not in new_parent_span_id:
            # Has parent: try to find parent endpointName using segment-span index
            if new_parent_span_id in segment_id_span_to_endpoint_name:
                record_2['parent_endpoint_name'] = segment_id_span_to_endpoint_name[new_parent_span_id]
                stats['A'] = stats.get('A', 0) + 1
            else:
                record_2['parent_endpoint_name'] = '-2'  # Has parentSpanId but no record found
                stats['B'] = stats.get('B', 0) + 1
        else:
            # parentSpanId == -1 -> no parent
            record_2['parent_endpoint_name'] = '-1'
            stats['C'] = stats.get('C', 0) + 1

        records_2.append(record_2)

    # ========= Deduplicate: records_2 -> records_4 =========
    unique = {}
    for r in records_2:
        key = json.dumps(r, sort_keys=True, ensure_ascii=False)
        unique[key] = r
    records_4 = list(unique.values())
    logging.info('After deduplication: %d spans', len(records_4))

    # ========= Fix parent_endpoint_name: ensure parent endpoint exists in same trace =========
    tmp = set(f"{r['traceId']}-{r['endpointName']}" for r in records_4)
    records_5 = []
    for r in tqdm.tqdm(records_4, desc='build records_5'):
        key = f"{r['traceId']}-{r['parent_endpoint_name']}"
        if key in tmp:
            records_5.append(r)
        else:
            r2 = copy.deepcopy(r)
            r2['parent_endpoint_name'] = '-1'
            records_5.append(r2)

    os.makedirs(OUTPUT_RECORDS_DIR, exist_ok=True)
    with open(RECORDS_5_PATH, 'w', encoding='utf-8') as fout:
        for r in records_5:
            fout.write(json.dumps(r, ensure_ascii=False))
            fout.write('\n')
    logging.info('records_5 written to %s', RECORDS_5_PATH)

    # ========= Group by endpoint =========
    records_5_by_endpoints = {}
    for r in tqdm.tqdm(records_5, desc='group by endpoint'):
        e = r['endpointName']
        records_5_by_endpoints.setdefault(e, {'records': []})['records'].append(r)

    # ========= Filter out "slowed by downstream" timeouts: get records_3 =========
    timeout_free = {}  # Track endpoints with child timeouts in some traces

    for endpoint_name, bucket in records_5_by_endpoints.items():
        durations = sorted(r['duration'] + 5 for r in bucket['records'])
        if not durations:
            continue

        if len(durations) == 1:
            threshold_duration = durations[0]
        else:
            rates = [-1.0] + [durations[i] / durations[i - 1] for i in range(1, len(durations))]
            max_rates = max(rates)
            max_rates_index = rates.index(max_rates)
            threshold_duration = durations[max_rates_index]

            # If no obvious jump, this endpoint doesn't use duration for timeout detection
            if max_rates < 3:
                threshold_duration = math.inf

        for r in bucket['records']:
            status_code = r.get('statusCode', 0) or 0
            # Key logic: duration exceeds threshold OR statusCode != 0 -> considered timeout
            r['timeout'] = (r['duration'] >= threshold_duration) or (status_code != 0)

            if r['timeout'] and r['parent_endpoint_name'] != '-1':
                p = r['parent_endpoint_name']
                timeout_free.setdefault(p, []).append(r['traceId'])

    timeout_free_2 = {k: set(v) for k, v in timeout_free.items()}

    records_3 = []
    total = sum(len(v['records']) for v in records_5_by_endpoints.values())
    tqdm_context = tqdm.tqdm(total=total, desc='build records_3')
    stats = {}

    for endpoint_name, bucket in records_5_by_endpoints.items():
        for r in bucket['records']:
            tqdm_context.update(1)
            if not r.get('timeout', False):
                stats['C'] = stats.get('C', 0) + 1
                continue

            # If this endpoint has child timeout in trace, not root cause, just propagated
            if endpoint_name in timeout_free_2 and r['traceId'] in timeout_free_2[endpoint_name]:
                stats['A'] = stats.get('A', 0) + 1
            else:
                stats['B'] = stats.get('B', 0) + 1
                records_3.append(r)

    tqdm_context.close()
    logging.info('After timeout filtering: %d candidate root-cause spans', len(records_3))

    with open(RECORDS_3_PATH, 'w', encoding='utf-8') as fout:
        for r in records_3:
            fout.write(json.dumps(r, ensure_ascii=False))
            fout.write('\n')
    logging.info('records_3 written to %s', RECORDS_3_PATH)

    # ========= Aggregate by endpoint for plotting =========
    records_by_endpoint = {}
    for c in records_3:
        e = c['endpointName']
        info = records_by_endpoint.setdefault(e, {
            'cnt': 0,
            'min_start_time': math.inf,
            'max_start_time': -1,
            'min_duration': math.inf,
            'records': [],
        })
        info['cnt'] += 1
        info['min_start_time'] = min(info['min_start_time'], c['startTime'])
        info['max_start_time'] = max(info['max_start_time'], c['startTime'])
        info['min_duration'] = min(info['min_duration'], c['duration'])
        info['records'].append(c)

    for e, info in records_by_endpoint.items():
        info['duration'] = info['max_start_time'] - info['min_start_time']

    # ========= Generate scatter plots & histograms =========
    shutil.rmtree(OUTPUT_FIG_DIR, ignore_errors=True)
    os.makedirs(OUTPUT_FIG_DIR, exist_ok=True)

    for i, (e, info) in enumerate(records_by_endpoint.items()):
        xs = [(r['startTime'] - min_time) / 1000.0 / 60.0 for r in info['records']]
        ys = [r['duration'] for r in info['records']]

        plt.figure()
        plt.scatter(xs, ys)
        plt.xlabel('time (minutes since first span)')
        plt.ylabel('duration (ms)')
        plt.title(f'scatter for {e}')
        plt.tight_layout()
        plt.savefig(os.path.join(OUTPUT_FIG_DIR, f'{i}_scatter.png'))
        plt.close()

        plt.figure()
        plt.hist(xs, bins=30)
        plt.xlabel('time (minutes since first span)')
        plt.ylabel('count')
        plt.title(f'hist for {e}')
        plt.tight_layout()
        plt.savefig(os.path.join(OUTPUT_FIG_DIR, f'{i}_hist.png'))
        plt.close()

    logging.info('All done.')


if __name__ == '__main__':
    main()
