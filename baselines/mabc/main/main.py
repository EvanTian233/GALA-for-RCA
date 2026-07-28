
from mABC.agents.base.profile import DataDetective, DependencyExplorer, ProbabilityOracle, FaultMapper, AlertReceiver, ProcessScheduler, SolutionEngineer
from mABC.agents.base.run import ReActTotRun, ThreeHotCotRun, BaseRun
from mABC.agents.tools import process_scheduler_tools, alert_receiver_tools, solution_engineer_tools

import os
import re
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


RUNS_DIR = (Path(__file__).resolve().parents[1] / "runs")
RUNS_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY_PATH = RUNS_DIR / "summary.jsonl"

# ============================================================
# ROOTCAUSE_WHITELIST Configuration
# Select ONE of the following whitelists based on dataset:
# - RE2-OB: Online Boutique microservices
# - RE2-TT: TrainTicket microservices
# - Dataset3: Dataset3 benchmark services
# - OpenRCA: OpenRCA Bank dataset
# ============================================================

# ------ Dataset3: Dataset3 benchmark services ------
# Dataset3 has no inject_time; root cause candidates must match dataset3 service naming.
ROOTCAUSE_WHITELIST = [
    "accounting", "ad", "cart", "checkout", "currency", "email", "fraud", "fraud-detection",
    "frontend", "image", "image-provider", "opensearch", "payment", "postgresql", "product",
    "product-catalog", "quote", "recommendation", "recommendation_flagd", "shipping",
]

# ------ RE2-TT: TrainTicket Services ------
# ROOTCAUSE_WHITELIST = [
#     "ts-auth-service",
#     "ts-order-service",
#     "ts-order-other-service",
#     "ts-route-service",
#     "ts-route-plan-service",
#     "ts-train-service",
#     "ts-travel-service",
#     "ts-travel2-service",
#     "ts-travel-plan-service",
#     "ts-seat-service",
#     "ts-station-service",
#     "ts-basic-service",
#     "ts-config-service",
#     "ts-contacts-service",
#     "ts-consign-service",
#     "ts-consign-price-service",
#     "ts-execute-service",
#     "ts-preserve-service",
#     "ts-preserve-other-service",
#     "ts-price-service",
#     "ts-security-service",
#     "ts-ticketinfo-service",
#     "ts-user-service",
#     "ts-payment-service",
#     "ts-rebook-service",
#     "ts-cancel-service",
#     "ts-food-service",
#     "ts-food-map-service",
#     "ts-assurance-service",
#     "ts-admin-basic-info-service",
#     "ts-admin-order-service",
#     "ts-admin-route-service",
#     "ts-admin-travel-service",
#     "ts-admin-user-service",
#     "ts-inside-payment-service",
#     "ts-notification-service",
#     "ts-verification-code-service",
#     "ts-voucher-service",
#     "ts-news-service",
#     "ts-avatar-service",
#     "ts-ticket-office-service",
#     "ts-ui-dashboard",
# ]

# ------ Dataset3 (no inject_time; use when running batch_rcaeval_dataset3.py) ------
# ROOTCAUSE_WHITELIST = [
#     "accounting", "ad", "cart", "checkout", "currency", "email", "fraud", "fraud-detection",
#     "frontend", "image", "image-provider", "opensearch", "payment", "postgresql", "product",
#     "product-catalog", "quote", "recommendation", "recommendation_flagd", "shipping",
# ]

# ------ Legacy/Other whitelists (for reference) ------
### ROOTCAUSE_WHITELIST = ["IG01",
#       "IG02",
#       "MG01",
#       "MG02",
#       "Tomcat03",
#       "Tomcat04",
#       "dockerA1",
#       "dockerA2",
#      "dockerB1",
#       "dockerB2",
#       "apache01",
#       "apache02",
#       "Redis01",
#      "Redis02"]

ROOTCAUSE_WHITELIST_STR = ", ".join(ROOTCAUSE_WHITELIST)

def to_iso_utc_str(t_str: str) -> str:

    s = str(t_str).strip()
    if re.fullmatch(r"\d{10}", s):  # seconds epoch
        dt = datetime.fromtimestamp(int(s), tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    if re.fullmatch(r"\d{13}", s):  # ms epoch
        dt = datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    # assume 'YYYY-MM-DD HH:MM:SS'
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

def minute_key_from_any(t_str: str) -> str:
    s = str(t_str).strip()
    if re.fullmatch(r"\d{10}", s):
        dt = datetime.fromtimestamp(int(s), tz=timezone.utc)
    elif re.fullmatch(r"\d{13}", s):
        dt = datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
    else:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    dt = dt.replace(second=0, microsecond=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def safe_filename(s: str) -> str:
    return re.sub(r"[^\w\-.]+", "_", s)

def write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def append_summary_line(path: Path, line_obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line_obj, ensure_ascii=False) + "\n")

def try_parse_json(text: str):
    """
    Parse JSON from agent's final output. Returns None if no valid JSON found.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        chunk = text[start:end+1]
        try:
            return json.loads(chunk)
        except Exception:
            return None
    return None

# ---------------------------
# Main Process
# ---------------------------

if __name__ == "__main__":
    # question = "16 + 23 * 44 + 99 / 9 = ?"
    # question = "1 - 9 + 2 * 16 + 99 / 9 * 8 + 7 - 6 = ?"
    # T = "2024-01-09 09:00:00"
    # ENDPOINT = "ts-travel-plan-service-/api/v1/routeplanservice/routePlan/quickestRoute"
    i = 0
    label_path = os.environ.get("LABEL_FILE", "data/label/label.json")
    print(f"[DEBUG] Using label_path = {label_path}")
    with open(label_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for t, v in data.items():
        for endpoint, path in v.items():
            print("@" * 30, "Decision Maker", "@" * 30)
            question = f"""Backgroud: In a distributed microservices system, there is a lot of traces across endpoints which represent the dependency relationship between endpoints. A trace consists of a sequence of spans, each representing a call from one endpoint to another when ignore the service level. 

            Alert generally occurs on the top endpoint at time T for a significant anomaly when the root cause endpoint at time T' is the downstream endpoint of the alerting endpoint. Endpoint A(TA) -> Endpoint B(TB) -> Endpoint C(TC) -> Endpoint D(TD), if the alert occurs on the Endpoint A at time TA, the root cause endpoint is the Endpoint C at time TC when the metric of Endpoint C is abnormal but the metric of Endpoint D at time TD is normal.

            Alert: Endpoint {endpoint} experiencing a significant increase in response time {t}. 
            Task: Please find the root cause endpoint behind the alerting endpoint {endpoint} by analyzing the metric of endpoint and the call trace. 
            CONSTRAINT (IMPORTANT):
            - Your root cause MUST be chosen from this whitelist (service names only):
              {ROOTCAUSE_WHITELIST_STR}
            - Do NOT invent any service name outside the whitelist.

            Format: Root Cause Endpoint: XXX, Root Cause Reason: XXX
            
            """
            print(f"Q: {question}")
            # sort
            # answer = ReActTotRun.run(agent=AlertReceiver(), question=question, agent_tool_env=vars(alert_receiver_tools))
            agent = ProcessScheduler()
            run = ReActTotRun()
            eval_run = ThreeHotCotRun(0, 0)
            agents = [DataDetective(), DependencyExplorer(), ProbabilityOracle(), FaultMapper(), AlertReceiver(),
                      ProcessScheduler(), SolutionEngineer()]
            answer = run.run(agent=agent, question=question, agent_tool_env=vars(process_scheduler_tools),
                             eval_run=eval_run, agents=agents)
            print(f"A: {answer}")
            question = (
                    "Base on the analysis above, please first give the single most likely root cause "
                    "endpoint, and then provide a ranking of the top-5 candidate root cause endpoints.\n\n"
                    "CONSTRAINT (IMPORTANT):\n"
                    f"- The root cause and all Top-5 candidates MUST be chosen from this whitelist (service names only):\n"
                    f"  {ROOTCAUSE_WHITELIST_STR}\n"
                    "- Do NOT invent any service name outside the whitelist.\n"
                    "Format:\n"
                    "Root Cause Endpoint: <best single endpoint>\n"
                    "Root Cause Reason: <reason for choosing this endpoint>\n"
                    "Top-5 Candidates (from most likely to least likely):\n"
                    "1. Endpoint: <endpoint_1>, Reason: <reason_1>\n"
                    "2. Endpoint: <endpoint_2>, Reason: <reason_2>\n"
                    "3. Endpoint: <endpoint_3>, Reason: <reason_3>\n"
                    "4. Endpoint: <endpoint_4>, Reason: <reason_4>\n"
                    "5. Endpoint: <endpoint_5>, Reason: <reason_5>\n"
                    "Please strictly follow the above format and do not output any other content.\n\n"
                    + answer
            )
            print(f"Q: {question}")

            agent = SolutionEngineer()
            agents = [SolutionEngineer()]
            answer = ReActTotRun().run(
                agent=agent,
                question=question,
                agent_tool_env=vars(solution_engineer_tools),
                eval_run=ThreeHotCotRun(),
                agents=agents,
            )
            print(f"A: {answer}")
            print("@" * 30, "Solution Engineer", "@" * 30)
            print("\n" * 20)

            i += 1
            if i >= 5:
                break



