Annotation Guidelines for RCAEval Gold Standard Generation
==========================================================

Purpose: Generate a high-precision Reference Summary and 3 Remediation Recommendations for incidents in the RCAEval dataset. These artifacts will serve as the "ground truth" for evaluating the Causal Faithfulness, Operational Feasibility, Diagnostic Precision, and Triage Logic of automated RCA tools.

Instructions:
1. Summary Requirements (Diagnostic Precision & Triage Logic)
- Chronological Narrative: Start with the injection timestamp and the specific service targeted.
- Failure Mechanism: Explicitly state the failure mode (e.g., memory leak, network delay) and how it propagates from the target service to downstream dependencies.
- Quantitative Impact: Since this is ground truth, you must simulate the expected impact based on the fault type:
    - Resource Faults (CPU/Mem): Describe specific usage spikes (e.g., "90% utilization") and resulting service degradation.
    - Network Faults (Delay/Loss): Quantify the latency increase (e.g., "300ms p99 spike") and error rates (e.g., "HTTP 504 timeouts").
    - Entity Naming: Use exact service names from the dataset. If a pod ID is not provided in the input, refer to the service's "active instances" or "affected deployment".

2. Recommendation Requirements (Operational Feasibility)
Provide exactly three prioritized steps that are technically feasible within a cloud-native microservice architecture:
- Action 1 (Immediate Mitigation): A step to stop the "bleeding" (e.g., restart service, scale replicas, or toggle a circuit breaker).
- Action 2 (Root Cause Remediation): A permanent fix for the specific failure type (e.g., "Patch memory leak in the Currency conversion logic," "Adjust socket timeout parameters").
- Action 3 (Preventative Observability): A context-specific monitoring or alerting rule to detect similar signatures in the future.

3. Causal Faithfulness Standards
- Avoid generic placeholders.
- Every recommendation must logically follow the identified failure type.
- Do not include "hallucinated" dependencies; only reference services typically involved in the standard target microservice flow.

==========================================================

Example for Ground Truth Generation:
Input Data:
    - Service: productcatalogservice
    - Failure Type: socket errors
    - Injection Time: 2024-06-12T10:15:00Z

Summary: 
At 10:15:00Z, a socket errors fault was injected into the productcatalogservice, causing a sudden termination of persistent TCP connections. This resulted in an immediate 15% spike in HTTP 500 errors on the frontendservice as it failed to retrieve product metadata. Trace data would show multiple ConnectionReset exceptions propagating from the catalog service, leading to cascading failures in the recommendation engine's retrieval logic.

Recommendations:
- Immediate Mitigation: Implement a retry policy with exponential backoff on the frontendservice client for catalog requests to handle transient socket resets.
- Permanent Fix: Audit the socket configuration in productcatalogservice to ensure keep-alive parameters and connection pool limits match the peak traffic demands observed during the fault.
- Prevention: Configure a Prometheus alert to trigger if the grpc_server_handled_total metric for productcatalogservice shows a non-zero rate of Internal or Unavailable codes for >60 seconds.