cand = """## POSSIBLE ROOT CAUSE REASONS:

- high CPU usage
- high memory usage 
- network latency 
- network packet loss
- high disk I/O usage 
- high socket usage

## POSSIBLE ROOT CAUSE COMPONENTS:

- ts-admin-basic-info-service
- ts-admin-order-service
- ts-admin-route-service
- ts-admin-travel-service
- ts-admin-user-service
- ts-assurance-mongo
- ts-assurance-service
- ts-auth-mongo
- ts-auth-service
- ts-avatar-service
- ts-basic-service
- ts-cancel-service
- ts-config-mongo
- ts-config-service
- ts-consign-mongo
- ts-consign-price-mongo
- ts-consign-price-service
- ts-consign-service
- ts-contacts-mongo
- ts-contacts-service
- ts-execute-service
- ts-food-map-mongo
- ts-food-map-service
- ts-food-mongo
- ts-food-mysql
- ts-food-service
- ts-inside-payment-mongo
- ts-inside-payment-service
- ts-news-service
- ts-notification-mongo
- ts-notification-service
- ts-order-mongo
- ts-order-other-mongo
- ts-order-other-service
- ts-order-service
- ts-payment-mongo
- ts-payment-service
- ts-preserve-mongo
- ts-preserve-other-mongo
- ts-preserve-other-service
- ts-preserve-service
- ts-price-mongo
- ts-price-service
- ts-rebook-service
- ts-route-mongo
- ts-route-plan-service
- ts-route-service
- ts-seat-service
- ts-security-mongo
- ts-security-service
- ts-station-mongo
- ts-station-service
- ts-ticket-office-mongo
- ts-ticket-office-service
- ts-ticketinfo-service
- ts-train-mongo
- ts-train-service
- ts-travel-mongo
- ts-travel-plan-service
- ts-travel-service
- ts-travel2-mongo
- ts-travel2-service
- ts-ui-dashboard
- ts-user-mongo
- ts-user-service
- ts-verification-code-service
- ts-voucher-mysql
- ts-voucher-service"""

schema = f"""## TELEMETRY DIRECTORY STRUCTURE:

- You can access the telemetry directory in our microservices system: `__RE2_TT_ROOT__/`.

- This directory contains multiple case folders organized by injected fault types. Each case folder name follows the pattern: `<faulty_service>_<fault_type>` (e.g., `ts-auth-service_cpu`, `ts-order-service_delay`, `ts-travel-service_socket`).

- Within each case folder, there are multiple numbered subdirectories (e.g., `__RE2_TT_ROOT__/ts-auth-service_cpu/1/`). Each numbered subdirectory corresponds to one case instance.

- Within each case instance directory, you'll find the telemetry files **directly** (not in subdirectories):
  - `simple_metrics.csv` - service-level metrics
  - `logs.csv` - structured log data
  - `traces.csv` - distributed tracing spans

- **IMPORTANT - How to find the correct case**: The query specifies a time range (e.g., "between 10:05:25 and 10:11:25 UTC on January 22, 2024"). To locate the relevant telemetry:
  1. Each case folder corresponds to ONE fault injection event.
  2. You need to scan through case folders and check if the metric file's timestamps fall within the query's time range.
  3. Use `os.listdir()` to iterate through case directories, then load each `simple_metrics.csv` file and check the `time` column (Unix timestamp in seconds).
  4. Once you find a case whose data timestamps match the query's time range, analyze that case's telemetry to identify the root cause component.

## DATA SCHEMA

1.  **Metric File** (`simple_metrics.csv`):

    - A service-level metric table with a single `time` column and multiple KPI columns.
    - `time` is an integer timestamp (seconds).
    - Each KPI column name follows the pattern: `<service>_<kpi>`.
    - Available KPI types: `cpu`, `mem`, `diskio`, `socket`, `workload`, `error`, `latency-50`, `latency-90`.

    ```csv
    time,ts-auth-service_cpu,ts-order-service_cpu,ts-travel-service_cpu,...,ts-auth-service_mem,ts-order-service_mem,...,ts-auth-service_latency-90,...
    1705918105,25.3,12.1,8.5,...,45.2,38.7,...,0.023,...
    ```

2.  **Trace File** (`traces.csv`):

    - A span-level distributed tracing table.

    ```csv
    time,traceID,spanID,serviceName,methodName,operationName,startTimeMillis,startTime,duration,statusCode,parentSpanID
    09:56,abc123,def456,ts-seat-service,POST,POST /api/v1/seatservice/seats/left_tickets,1705918585000,1705918585000000,63643,NaN,ghi789
    ```

    - `duration` is in microseconds.
    - `startTimeMillis` is in milliseconds, `startTime` is in microseconds.

3.  **Log File** (`logs.csv`):

    - A structured log table from all services.

    ```csv
    time,timestamp,container_name,message,level,req_path,error
    09:56,1705918585001234567,ts-seat-service,Seat request To String: Seat{{...}},NaN,/api/v1/seatservice,NaN
    ```

    - `timestamp` is in nanoseconds.
    - `container_name` indicates which service generated the log.

{cand}

## CLARIFICATION OF TELEMETRY DATA:

1. This microservice system is a **Train Ticket booking platform** (similar to 12306). It handles train ticket reservations, route planning, seat allocation, payment processing, and related operations.

2. The `simple_metrics.csv` table contains service-level KPIs for all 68 components (both application services and database services). The KPI types include:
   - `cpu`: CPU usage percentage
   - `mem`: Memory usage percentage
   - `diskio`: Disk I/O usage
   - `socket`: Socket connection count
   - `workload`: Request workload/throughput
   - `error`: Error rate
   - `latency-50`: 50th percentile latency
   - `latency-90`: 90th percentile latency

3. In different telemetry files, the timestamp units vary:
   - Metrics (`simple_metrics.csv` `time` column): timestamp is in **seconds** (e.g., 1705918105).
   - Traces (`startTimeMillis`): timestamp is in **milliseconds** (e.g., 1705918585000).
   - Traces (`startTime`): timestamp is in **microseconds** (e.g., 1705918585000000).
   - Traces (`duration`): duration is in **microseconds**.
   - Logs (`timestamp`): timestamp is in **nanoseconds** (e.g., 1705918585001234567).

4. Time zone:
   - The numeric timestamps behave like standard Unix epoch time in **UTC (UTC+0)**.
   - If you need Beijing time for presentation, convert from UTC to `Asia/Shanghai` (UTC+8).

5. Root cause analysis tips:
   - Look for anomalous spikes in CPU, memory, disk I/O, or socket usage.
   - Check for increased latency (latency-50, latency-90) or error rates.
   - Correlate metrics with trace data to identify the faulty service.
   - The root cause component is typically the service showing abnormal behavior during the fault injection window.

## CRITICAL DATA LOADING RESTRICTIONS (MUST FOLLOW):

To minimize token usage and avoid memory issues, you MUST follow these rules when loading telemetry data:

### 1. TIME WINDOW FILTERING (MANDATORY)
After loading any CSV file, IMMEDIATELY filter by the query's time window. DO NOT analyze unfiltered data.

```python
# Example: Query time window is 10:05:25 to 10:11:25 UTC on 2024-01-22
start_ts = 1705914325  # Convert query start time to Unix timestamp (seconds)
end_ts = 1705914685    # Convert query end time to Unix timestamp (seconds)

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
