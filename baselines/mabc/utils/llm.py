from mABC.settings import OPENAI_API_KEY, OPENAI_MAX_RETRIES, OPENAI_RETRY_SLEEP, OPENAI_MODEL
from openai import OpenAI
import time

# NEW
import os
import json


def _append_usage_log(entry: dict) -> None:

    log_path = os.environ.get("LLM_USAGE_LOG", "").strip()
    if not log_path:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False))
        f.write("\n")


def _extract_usage_tokens(completion) -> dict:
    u = getattr(completion, "usage", None)
    if u is None:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    input_tokens = getattr(u, "prompt_tokens", None)
    output_tokens = getattr(u, "completion_tokens", None)
    total_tokens = getattr(u, "total_tokens", None)

    if input_tokens is None and isinstance(u, dict):
        input_tokens = u.get("prompt_tokens", u.get("input_tokens"))
    if output_tokens is None and isinstance(u, dict):
        output_tokens = u.get("completion_tokens", u.get("output_tokens"))
    if total_tokens is None and isinstance(u, dict):
        total_tokens = u.get("total_tokens")

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def llm_chat(shared_messages, stop_words):
    for i in range(OPENAI_MAX_RETRIES):
        try:
            client = OpenAI(
                api_key=OPENAI_API_KEY,
            )
            completion = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=shared_messages,
                # stop=stop_words,
                # max_tokens=4096,
            )

            # NEW: write usage tokens to jsonl (STRICTLY from completion.usage)
            toks = _extract_usage_tokens(completion)
            _append_usage_log({
                "model": getattr(completion, "model", None) or OPENAI_MODEL,
                "completion_id": getattr(completion, "id", None),
                "usage": toks,  # includes input_tokens / output_tokens / total_tokens
            })

            print(completion)
            return completion.choices[0].message.content

        except Exception as e:
            print(e)
            time.sleep(OPENAI_RETRY_SLEEP)
            continue
    return "Connection error."


if __name__ == "__main__":
    shared_messages = [
        {"role": "system", "content": "You are a poetic assistant, skilled in explaining complex programming concepts with creative flair."},
    ]
    shared_messages.append({"role": "user", "content": "Compose a poem that explains the concept of recursion in programming."})
    answer = llm_chat(shared_messages, "")
    shared_messages.append({"role": "assistant", "content": answer})
    print(shared_messages, answer)

