cand = """## POSSIBLE ROOT CAUSE REASONS:

- high CPU usage
- high memory usage 
- network latency 
- network packet loss
- high disk I/O read usage 
- high disk space usage
- high JVM CPU load 
- JVM Out of Memory (OOM) Heap
- high socket usage

## POSSIBLE ROOT CAUSE COMPONENTS:

- adservice
- cartservice
- currencyservice
- productcatalogservice
- checkoutservice
- recommendationservice
- shippingservice
- emailservice
- paymentservice"""

schema = f"""## TELEMETRY DIRECTORY STRUCTURE:

- You can access the telemetry directory in our microservices system: `__RE2_OB_ROOT__/`.

- This directory contains multiple case folders organized by injected fault types. Each case folder name follows the pattern: `<faulty_service>_<fault_type>` (e.g., `checkoutservice_cpu`, `checkoutservice_socket`, `productcatalogservice_cpu`).

- Within each case folder, there are multiple numbered subdirectories (e.g., `__RE2_OB_ROOT__/checkoutservice_cpu/2/`). Each numbered subdirectory corresponds to one case instance.

- Within each case instance directory, you’ll find these subdirectories: `simple_metrics`, `traces`, and `logs` (e.g., `__RE2_OB_ROOT__/checkoutservice_cpu/2/simple_metrics/`).

- Each of the three subdirectories contains the corresponding telemetry file(s). If you are unsure about the exact file name, first use Python `os.listdir()` to locate the file, and then load it with pandas.

- **Important note about file format**: some telemetry files may be saved by spreadsheet software (e.g., WPS/Excel) and are actually `.xlsx`/`.xls` files even if the filename ends with `.csv`. If you encounter a decoding/parsing error when using `pd.read_csv()`, try `pd.read_excel()`.

- **IMPORTANT - How to find the correct case**: The query specifies a time range (e.g., "between 21:33:06 and 21:39:06 UTC on January 15, 2024"). To locate the relevant telemetry:
  1. Each case folder corresponds to ONE fault injection event.
  2. You need to scan through case folders and check if the metric file's timestamps fall within the query's time range.
  3. Use `os.listdir()` to iterate through case directories, then load each `simple_metrics` file and check the `time` column (Unix timestamp in seconds).
  4. Once you find a case whose data timestamps match the query's time range, analyze that case's telemetry to identify the root cause component.

## DATA SCHEMA

1.  **Metric Files**:

    1. Files under `simple_metrics/`:

        - A service-level metric table with a single `time` column and multiple KPI columns.
        - `time` is an integer timestamp (seconds).
        - Each KPI column name follows the pattern: `<service>_<kpi>` (e.g., `productcatalogservice_cpu`, `checkoutservice_socket`, `shippingservice_latency-90`).

        ```csv
        time,adservice_cpu,cartservice_cpu,checkoutservice_cpu,currencyservice_cpu,emailservice_cpu,paymentservice_cpu,productcatalogservice_cpu,recommendationservice_cpu,shippingservice_cpu,...,checkoutservice_latency-90,productcatalogservice_latency-90,...
        1705339822,0.339336,1.161912,0.230925,14.675869,0.227218,0.220750,0.727713,2.066548,0.271931,...,0.1825,0.004432,...
        ```

2.  **Trace Files**:

    1. Files under `traces/`:

        - A span-level trace table.

        ```csv
        time,traceID,spanID,serviceName,methodName,operationName,startTimeMillis,startTime,duration,statusCode,parentSpanID
        18:57:00,f7995da8911b2bbc0426fe46b035348a,776ab1092623ec5a,currencyservice,Convert,grpc.hipstershop.CurrencyService/Convert,1705345045000,1705345045000000,109,0,3a4dc1f415104530
        ```

3.  **Log Files**:

    1. Files under `logs/`:

        - A structured log table.

        ```csv
        time,timestamp,container_name,message,level,req_path,error,cluster_id,log_template
        18:57:00,1705345045003290449,currencyservice,conversion request successful,info,,,1,currencyservice conversion request successful
        ```

{cand}

## CLARIFICATION OF TELEMETRY DATA:

1. This microservice system is an online-shopping (e-commerce) style application.

2. The `simple_metrics` table contains multiple service-level KPIs (CPU, memory, socket, workload, latency percentiles, etc.). Some columns may include non-candidate components (e.g., `frontend`, `frontend-external`, `redis`). However, the root cause component MUST be selected from the provided candidates above.

3. In different telemetry files, the timestamp units may vary:

- Metrics (`simple_metrics.time`): timestamp is in seconds (e.g., 1705339822).

- Traces (`startTimeMillis` / `startTime`): `startTimeMillis` is in milliseconds (e.g., 1705345045000), and `startTime` is in microseconds (e.g., 1705345045000000).

- Logs (`timestamp`): timestamp is in nanoseconds (e.g., 1705345045003290449).

4. Time zone:
- The numeric timestamps behave like standard Unix epoch time in **UTC (UTC+0)**.
- If you need Beijing time for presentation, convert from UTC to `Asia/Shanghai` (UTC+8).

## CRITICAL DATA LOADING RESTRICTIONS (MUST FOLLOW):

To minimize token usage and avoid memory issues, you MUST follow these rules when loading telemetry data:

### 1. TIME WINDOW FILTERING (MANDATORY)
After loading any CSV file, IMMEDIATELY filter by the query's time window. DO NOT analyze unfiltered data.

```python
# Example: Query time window is 21:33:06 to 21:39:06 UTC on 2024-01-15
start_ts = 1705354386  # Convert query start time to Unix timestamp (seconds)
end_ts = 1705354746    # Convert query end time to Unix timestamp (seconds)

# For metrics (time in seconds):
df_metrics = pd.read_csv(metrics_path)
df_metrics = df_metrics[(df_metrics['time'] >= start_ts) & (df_metrics['time'] <= end_ts)]

# For traces (startTimeMillis in milliseconds):
df_traces = pd.read_csv(traces_path)
df_traces = df_traces[(df_traces['startTimeMillis'] >= start_ts * 1000) & (df_traces['startTimeMillis'] <= end_ts * 1000)]

# For logs (timestamp in nanoseconds):
df_logs = pd.read_csv(logs_path)
df_logs = df_logs[(df_logs['timestamp'] >= start_ts * 1e9) & (df_logs['timestamp'] <= end_ts * 1e9)]
```

### 2. LOGS RANDOM SAMPLING (MANDATORY)
Logs are typically very large. After time filtering, you MUST randomly sample 25% (1/4) of the logs:

```python
df_logs_filtered = df_logs[(df_logs['timestamp'] >= start_ts * 1e9) & (df_logs['timestamp'] <= end_ts * 1e9)]
df_logs_sampled = df_logs_filtered.sample(frac=0.25, random_state=42) if len(df_logs_filtered) > 100 else df_logs_filtered
```

### 3. OUTPUT RESTRICTIONS (MANDATORY)
- NEVER use `print(df)` or `df.head(50)` to display raw data.
- Use ONLY statistical summaries: `df.describe()`, `df.mean()`, `df.max()`, `df.groupby().agg()`.
- When showing examples, use `df.head(5)` (maximum 5 rows).
- For column inspection, use `df.columns.tolist()` instead of printing the DataFrame.

### 4. EFFICIENT CASE SEARCHING
When searching for the correct case folder, DO NOT load entire files. Instead:

```python
# Only load the 'time' column to check if timestamps match the query window
df_time_only = pd.read_csv(metrics_path, usecols=['time'])
if df_time_only['time'].min() <= end_ts and df_time_only['time'].max() >= start_ts:
    # This case matches the query time window
    pass
```

### 5. COLUMN SELECTION
When analyzing metrics, only load relevant columns:

```python
# Instead of loading all columns:
# df = pd.read_csv(path)  # BAD - loads all columns

# Load only needed columns:
cols_of_interest = ['time'] + [c for c in pd.read_csv(path, nrows=0).columns if 'cpu' in c or 'latency' in c]
df = pd.read_csv(path, usecols=cols_of_interest)  # GOOD - selective loading
```"""
