cand = """## POSSIBLE ROOT CAUSE REASONS:

- high CPU usage
- high memory usage
- network latency (delay)
- network packet loss
- high disk I/O usage

## POSSIBLE ROOT CAUSE COMPONENTS:

- accounting
- ad
- cart
- checkout
- currency
- email
- fraud-detection (fraud)
- frontend
- image
- opensearch
- payment
- postgresql
- product
- quote
- recommendation
- shipping"""

schema = f"""## TELEMETRY DATA ACCESS:

- The telemetry data for each case is stored in a dedicated directory.
- Each case directory contains:
  - `metrics.csv` - service-level metrics
  - `logs.csv` - structured log data
  - `traces.csv` - distributed tracing spans

- **IMPORTANT**: The case directory path will be provided in the query. Use that path to access the data files.

## DATA SCHEMA

1. **Metric File** (`metrics.csv`):

    - A service-level metric table with a `timestamp` column and multiple KPI columns.
    - `timestamp` is an integer Unix timestamp (seconds).
    - Each KPI column name follows the pattern: `<service>_<metric>`.
    - Available metrics per service: `cpu`, `mem`, `diskio`, `network_io_bytes_per_sec`, `latency_p50`, `latency_p90`.

    ```csv
    timestamp,accounting_cpu,accounting_mem,ad_cpu,ad_mem,...,cart_latency_p50,cart_latency_p90,...
    1768905220,0.046,111.17,0.002,187.29,...,1.73,6.49,...
    ```

2. **Trace File** (`traces.csv`):

    - A span-level distributed tracing table.

    ```csv
    time,trace_id,span_id,parent_span_id,service,method,operation,kind,start_time_us,duration_us,duration_ms,http_status,http_method,http_url
    10:33,ddd121edafddda5623f78985d7bc7bbb,54d79cb40d86a53b,d6a2c89eefea527e,frontend,NaN,GET,2,1768905220036000,32339,32.339,NaN,GET,http://...
    ```

    - `duration_us` is in microseconds, `duration_ms` is in milliseconds.
    - `start_time_us` is in microseconds.

3. **Log File** (`logs.csv`):

    - A structured log table from all services.

    ```csv
    time,timestamp,container_name,message,level,req_path
    10:36,1768905367,accounting,Order details: {{@OrderResult}}.,information,NaN
    ```

    - `timestamp` is in seconds.
    - `container_name` indicates which service generated the log.

{cand}

## CLARIFICATION OF TELEMETRY DATA:

1. This microservice system is an **OpenTelemetry Demo e-commerce application**. It handles product browsing, shopping cart, checkout, payment, and related operations.

2. The `metrics.csv` table contains service-level KPIs for all components. The metric types include:
   - `cpu`: CPU usage percentage
   - `mem`: Memory usage (MB)
   - `diskio`: Disk I/O bytes
   - `network_io_bytes_per_sec`: Network I/O throughput
   - `latency_p50`: 50th percentile latency
   - `latency_p90`: 90th percentile latency

3. Timestamp units:
   - Metrics (`timestamp` column): Unix timestamp in **seconds**
   - Traces (`start_time_us`): Unix timestamp in **microseconds**
   - Logs (`timestamp` column): Unix timestamp in **seconds**

4. Time zone:
   - The numeric timestamps behave like standard Unix epoch time in **UTC (UTC+0)**.

5. Root cause analysis approach:
   - Analyze the metrics to find anomalous patterns in CPU, memory, disk I/O, network usage, or latency.
   - Use traces to understand the call chain and identify slow or failing services.
   - Use logs to find error messages or abnormal patterns.
   - **IMPORTANT**: Since there is no specific injection_time, analyze the ENTIRE metrics file. Look for sustained anomalies or abnormal patterns compared to baseline behavior.

## CRITICAL DATA LOADING INSTRUCTIONS (MUST FOLLOW):

### 1. NO TIME FILTERING NEEDED
Analyze the ENTIRE metrics file without time filtering. Look for services with abnormal behavior patterns.

### 2. LOGS RANDOM SAMPLING (MANDATORY)
Logs are typically very large. You MUST randomly sample 25% (1/4) of the logs:

```python
df_logs = pd.read_csv(logs_path)
df_logs_sampled = df_logs.sample(frac=0.25, random_state=42) if len(df_logs) > 100 else df_logs
```

### 3. OUTPUT RESTRICTIONS (MANDATORY)
- NEVER use `print(df)` or `df.head(50)` to display raw data.
- Use ONLY statistical summaries: `df.describe()`, `df.mean()`, `df.max()`, `df.groupby().agg()`.
- When showing examples, use `df.head(5)` (maximum 5 rows).
- For column inspection, use `df.columns.tolist()` instead of printing the DataFrame.

### 4. DATA ACCESS
Read data files from the case directory path provided in the query. The path will be in format like `...Dataset3/data/case_XXX`:

```python
case_path = '<path from query>'  # Extract the path from the instruction
metrics_df = pd.read_csv(f'{{case_path}}/metrics.csv')
logs_df = pd.read_csv(f'{{case_path}}/logs.csv')
traces_df = pd.read_csv(f'{{case_path}}/traces.csv')
```

### 5. ANALYSIS APPROACH
1. Calculate statistics for each service's metrics (mean, std, max, min)
2. Compare services to identify which one shows the most abnormal behavior
3. Use traces to understand the call chain and identify bottlenecks
4. Use logs to find error messages or abnormal patterns
5. Synthesize findings to determine the root cause component"""
