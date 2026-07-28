# refactor from https://github.com/IntelligentDDS/MicroRank
import os
import sys
import csv
import json
import math
import time
import datetime
import codecs
import warnings
warnings.filterwarnings("ignore")
from copy import deepcopy

from tqdm import tqdm
import numpy as np
from dateutil.parser import parse
import pandas as pd


def get_operation_slo(span_df):
    """ Calculate the mean of duration and variance of each operation
    :arg
        span_df: span data with operations and duration columns
    :return
        operation dict of the mean of and variance 
        {
            # operation: {mean, variance}
            "Currencyservice_Convert": [600, 3]}
        }   
    """
    operation_slo = {}
    for op in span_df["operation"].dropna().unique():
        #get mean and std of Duration column of the corresponding operation
        mean = round(span_df[span_df["operation"] == op]["duration"].mean() / 1_000, 2)
        std = round(span_df[span_df["operation"] == op]["duration"].std() / 1_000, 2)

        # operation_slo[op] = [mean, std]
        operation_slo[op] = { "mean": mean, "std": std }

    # print(json.dumps(operation_slo, sort_keys=True, indent=2))
    return operation_slo



def tracerca(data, inject_time=None, dataset=None, **kwargs):
    # Load traces.csv from the same directory as the metrics data file
    run_args = kwargs.get("args", None)
    if run_args is not None and hasattr(run_args, "data_path"):
        data_dir = os.path.dirname(run_args.data_path)
        trace_file = os.path.join(data_dir, "traces.csv")
        span_df = pd.read_csv(trace_file)
    else:
        span_df = data

    if "methodName" in span_df.columns:
        span_df["methodName"] = span_df["methodName"].fillna(
            span_df.get("operationName", span_df["methodName"])
        )
    elif "operationName" in span_df.columns:
        span_df["methodName"] = span_df["operationName"]
    else:
        raise KeyError("traces.csv has neither 'methodName' nor 'operationName' column")

    span_df["operation"] = span_df["serviceName"] + "_" + span_df["methodName"]

    # Normalise span end-time to seconds for comparison with inject_time (seconds).
    # Use startTimeMillis if available (ms -> s), otherwise auto-detect scale from startTime.
    if "startTimeMillis" in span_df.columns:
        span_end_seconds = span_df["startTimeMillis"] / 1_000 + span_df["duration"] / 1_000_000
    else:
        # Detect unit: if median startTime >> inject_time it's in microseconds
        median_start = span_df["startTime"].median()
        if inject_time and median_start > inject_time * 1_000:
            scale = 1_000_000  # microseconds -> seconds
        else:
            scale = 1  # already seconds
        span_end_seconds = span_df["startTime"] / scale + span_df["duration"] / (scale if scale > 1 else 1)

    span_df = span_df.copy()
    span_df["_end_s"] = span_end_seconds

    normal_df = span_df[span_df["_end_s"] < inject_time]
    anomal_df = span_df[span_df["_end_s"] >= inject_time]
    
    # 1. TRACE ANOMALY DETECTION
    normal_slo = get_operation_slo(normal_df)

    anomal_df["mean"] = anomal_df["operation"].apply(lambda op: normal_slo.get(op, {"mean": 0})["mean"])
    anomal_df["std"] = anomal_df["operation"].apply(lambda op: normal_slo.get(op, {"std": 0})["std"])
    anomal_df["abnormal"] = anomal_df["duration"] / 1_000 >= anomal_df["mean"] + 3 * anomal_df["std"]

    # 2. SUSPICIOUS MICROSERVICE SET MINING
    
    # calculate support and confidence
    operations = list(anomal_df.operation.unique())
    support_dict = {op: 0 for op in operations}
    confidence_dict = {op: 0 for op in operations}

    # support = |abnormal_traces of operation A| / |total abnormal traces|
    # abnormal traces of operation A is when "abnormal" col is True
    for op in operations:
        support_dict[op] = anomal_df[anomal_df["operation"] == op]["abnormal"].sum() / anomal_df["abnormal"].sum()
    
    # confidence = |abnormal traces of operation A| / |total traces of operation A|
    for op in operations:
        confidence_dict[op] = anomal_df[anomal_df["operation"] == op]["abnormal"].sum() / anomal_df[anomal_df["operation"] == op]["operation"].count()

    ji_dict = {op: 0 for op in operations} 
    # ji = 2 *s * c / (s + c)
    for op in operations:
        ji_dict[op] = 2 * support_dict[op] * confidence_dict[op] / (support_dict[op] + confidence_dict[op])

    # 3. MICROSERVICE RANKING
    # rank by ji 
    sorted_ji = sorted(ji_dict.items(), key=lambda x: x[1], reverse=True)
    
    ranks = []
    for op, ji in sorted_ji:
        # ignore ji nan 
        if np.isnan(ji):
            continue
        ranks.append(op)

    # print(top_list, score_list)
    return {
        "ranks": ranks,
    }
  


if __name__ == "__main__":
    main()