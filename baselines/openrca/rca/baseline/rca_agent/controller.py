import json
import re

from IPython.terminal.embed import InteractiveShellEmbed

from rca.api_router import get_chat_completion
from rca.baseline.rca_agent.executor import execute_act

system = """You are the Administrator of a DevOps Assistant system for failure diagnosis. To solve each given issue, you should iteratively instruct an Executor to write and execute Python code for data analysis on telemetry files of target system. By analyzing the execution results, you should approximate the answer step-by-step.

There is some domain knowledge for you:

{background}

{agent}

The issue you are going to solve is:

{objective}

Solve the issue step-by-step. In each step, your response should follow the JSON format below:

{format}

Let's begin."""

format = """{
    "analysis": (Your analysis of the code execution result from Executor in the last step, with detailed reasoning of 'what have been done' and 'what can be derived'. Respond 'None' if it is the first step.),
    "completed": ("True" if you believe the issue is resolved, and an answer can be derived in the 'instruction' field. Otherwise "False"),
    "instruction": (Your instruction for the Executor to perform via code execution in the next step. Do not involve complex multi-step instruction. Keep your instruction atomic, with clear request of 'what to do' and 'how to do'. Respond a summary by yourself if you believe the issue is resolved. Respond a summary by yourself if you believe the issue is resolved. Respond a summary by yourself if you believe the issue is resolved.)
}
(DO NOT contain "```json" and "```" tags. DO contain the JSON object with the brackets "{}" only. Use '\\n' instead of an actual newline character to ensure JSON compatibility when you want to insert a line break within a string.)"""

summary = """Now, you have decided to finish your reasoning process. You should now provide the final answer to the issue.

The candidates of possible root cause components and reasons are provided below. The root cause components and reasons MUST be selected from the provided candidates.

{cand}

Recall the issue is: {objective}

Please first review your previous reasoning process to infer the best answer. Then, output your final answer as **Top-5 ranked candidates** for each requested field.

IMPORTANT OUTPUT REQUIREMENTS:

1) Your response MUST contain ONLY ONE JSON object, wrapped by "```json" and "```" tags.
2) For each predicted root cause ("1", "2", ...), you MUST output three independent lists, each list contains exactly 5 items, ordered from most likely -> least likely:
   - "root cause occurrence datetime": ["...x5"]
   - "root cause component": ["...x5"]
   - "root cause reason": ["...x5"]
3) Each list MUST NOT contain duplicates.
4) Datetime format MUST be '%Y-%m-%d %H:%M:%S' in UTC+8 time zone.
5) "root cause component" and "root cause reason" items MUST come from the candidate lists above. Do NOT output 'unknown'/'null'/'not found'.
6) If you believe there is only one failure/root cause, output only key "1". If the issue indicates "unknown" or "more than one" failures, you may output keys "2", "3", ... as additional independent root causes.

JSON FORMAT EXAMPLE:

```json
{{
  "1": {{
    "root cause occurrence datetime": ["YYYY-MM-DD HH:MM:SS", "...", "...", "...", "..."],
    "root cause component": ["<candidate component>", "...", "...", "...", "..."],
    "root cause reason": ["<candidate reason>", "...", "...", "...", "..."]
  }},
  "2": {{
    "root cause occurrence datetime": ["YYYY-MM-DD HH:MM:SS", "...", "...", "...", "..."],
    "root cause component": ["<candidate component>", "...", "...", "...", "..."],
    "root cause reason": ["<candidate reason>", "...", "...", "...", "..."]
  }}
}}
```
"""


def control_loop(objective: str, plan: str, ap, bp, logger, max_step=5, max_turn=3, max_history_rounds=5) -> str:
    """Run the controller loop while trimming old chat history."""
    system_msg = {
        "role": "system",
        "content": system.format(
            objective=objective,
            format=format,
            agent=ap.rules,
            background=bp.schema,
        ),
    }
    prompt = [system_msg, {"role": "user", "content": "Let's begin."}]

    history = []
    trajectory = []
    kernel = InteractiveShellEmbed()
    init_code = (
        "import pandas as pd\n"
        "pd.set_option('display.width', 427)\n"
        "pd.set_option('display.max_columns', 10)\n"
    )
    kernel.run_cell(init_code)

    for step in range(max_step):
        max_history_msgs = max_history_rounds * 2
        if len(prompt) > 1 + max_history_msgs:
            truncated_prompt = [system_msg] + prompt[-max_history_msgs:]
            logger.debug(f"History truncated: {len(prompt)} -> {len(truncated_prompt)} messages")
        else:
            truncated_prompt = prompt

        note = [
            {
                "role": "user",
                "content": f"Continue reasoning for: {objective}\n\nResponse format:\n\n{format}",
            }
        ]
        attempt_actor = []
        response_raw = ""

        try:
            response_raw = get_chat_completion(messages=truncated_prompt + note)
            if "```json" in response_raw:
                response_raw = re.search(r"```json\n(.*)\n```", response_raw, re.S).group(1).strip()

            logger.debug(f"Raw Response:\n{response_raw}")

            if (
                '"analysis":' not in response_raw
                or '"instruction":' not in response_raw
                or '"completed":' not in response_raw
            ):
                logger.warning("Invalid response format. Please provide a valid JSON response.")
                prompt.append({"role": "assistant", "content": response_raw})
                prompt.append(
                    {"role": "user", "content": "Please provide your analysis in requested JSON format."}
                )
                continue

            response = json.loads(response_raw)
            analysis = response["analysis"]
            instruction = response["instruction"]
            completed = response["completed"]
            logger.info(
                "-" * 80
                + "\n"
                + f"### Step[{step + 1}]\nAnalysis: {analysis}\nInstruction: {instruction}"
                + "\n"
                + "-" * 80
            )

            if completed == "True":
                kernel.reset()
                prompt.append({"role": "assistant", "content": response_raw})
                prompt.append(
                    {"role": "user", "content": summary.format(objective=objective, cand=bp.cand)}
                )

                final_msgs = max_history_rounds * 2 + 2
                if len(prompt) > 1 + final_msgs:
                    final_prompt_truncated = [system_msg] + prompt[-final_msgs:]
                else:
                    final_prompt_truncated = prompt

                answer = get_chat_completion(messages=final_prompt_truncated)
                logger.debug(f"Raw Final Answer:\n{answer}")
                prompt.append({"role": "assistant", "content": answer})
                if "```json" in answer:
                    answer = re.search(r"```json\n(.*)\n```", answer, re.S).group(1).strip()
                return answer, trajectory, prompt

            code, result, status, new_history = execute_act(
                instruction,
                bp.schema,
                history,
                attempt_actor,
                kernel,
                logger,
            )
            if not status:
                logger.warning("Self-correction failed.")
                result = "The Executor failed to execute the instruction. Please provide a new instruction."

            history = new_history
            trajectory.append(
                {
                    "code": f"# In[{step + 1}]:\n\n{code}",
                    "result": f"Out[{step + 1}]:\n```\n{result}```",
                }
            )
            logger.info("-" * 80 + "\n" + f"Step[{step + 1}]\n### Observation:\n{result}" + "\n" + "-" * 80)
            prompt.append({"role": "assistant", "content": response_raw})
            prompt.append({"role": "user", "content": result})

        except Exception as error:
            logger.error(error)
            prompt.append({"role": "assistant", "content": response_raw})
            prompt.append(
                {
                    "role": "user",
                    "content": f"{str(error)}\nPlease provide your analysis in requested JSON format.",
                }
            )
            if "context_length_exceeded" in str(error):
                logger.warning("Token length exceeds the limit.")
                kernel.reset()
                return "Token length exceeds. No root cause found.", trajectory, prompt

    logger.warning("Max steps reached. Please check the history.")
    kernel.reset()
    final_prompt_content = summary.format(objective=objective, cand=bp.cand).replace(
        "Now, you have decided to finish your reasoning process. ",
        "Now, the maximum steps of your reasoning have been reached. ",
    )
    if prompt[-1]["role"] == "user":
        prompt[-1]["content"] = final_prompt_content
    else:
        prompt.append({"role": "user", "content": final_prompt_content})

    final_msgs = max_history_rounds * 2 + 2
    if len(prompt) > 1 + final_msgs:
        final_prompt_truncated = [system_msg] + prompt[-final_msgs:]
    else:
        final_prompt_truncated = prompt

    answer = get_chat_completion(messages=final_prompt_truncated)
    logger.debug(f"Raw Final Answer:\n{answer}")
    prompt.append({"role": "assistant", "content": answer})
    if "```json" in answer:
        answer = re.search(r"```json\n(.*)\n```", answer, re.S).group(1).strip()
    return answer, trajectory, prompt
