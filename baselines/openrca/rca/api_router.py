import os
import yaml
import time
import threading


PLACEHOLDER_CONFIG_VALUES = {
    "",
    "the/api/key",
    "the/path/to/api/base",
    "your_api_key",
    "your-api-key",
}


def _has_meaningful_value(value):
    if value is None:
        return False
    text = str(value).strip()
    return text not in PLACEHOLDER_CONFIG_VALUES


def load_config(config_path="rca/api_config.yaml"):
    configs = dict(os.environ)
    with open(config_path, "r") as file:
        yaml_data = yaml.safe_load(file) or {}
    for key, value in yaml_data.items():
        if _has_meaningful_value(value):
            configs[key] = value
    return configs


configs = load_config()

_case_ctx = threading.local()
_case_lock = threading.Lock()
_case_token_usage = {}


def set_active_case(case_id: str):
    """Set current case id for token aggregation in the current thread."""
    _case_ctx.case_id = case_id


def clear_active_case():
    """Clear current case id for token aggregation in the current thread."""
    if hasattr(_case_ctx, "case_id"):
        delattr(_case_ctx, "case_id")


def pop_case_token_usage(case_id: str):
    """Pop (and remove) aggregated token usage for a case."""
    with _case_lock:
        return _case_token_usage.pop(case_id, None)


def _get_active_case_id():
    return getattr(_case_ctx, "case_id", None)


def _extract_usage_tokens(usage):
    """
    Extract (input_tokens, output_tokens, total_tokens) from OpenAI-compatible usage.
    Supports both object-style and dict-style usage.
    """
    if usage is None:
        return None

    # Object-style (OpenAI Chat Completions): prompt_tokens / completion_tokens / total_tokens
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)

    # Dict-style
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
        completion_tokens = usage.get("completion_tokens", completion_tokens)
        total_tokens = usage.get("total_tokens", total_tokens)

    if prompt_tokens is None or completion_tokens is None or total_tokens is None:
        return None

    return int(prompt_tokens), int(completion_tokens), int(total_tokens)


def _record_usage(usage):
    case_id = _get_active_case_id()
    if not case_id:
        return

    tokens = _extract_usage_tokens(usage)
    if tokens is None:
        return

    in_tok, out_tok, total_tok = tokens

    with _case_lock:
        m = _case_token_usage.get(case_id)
        if m is None:
            m = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            _case_token_usage[case_id] = m
        m["input_tokens"] += in_tok
        m["output_tokens"] += out_tok
        m["total_tokens"] += total_tok


def OpenAI_chat_completion(messages, temperature):
    from openai import OpenAI
    client = OpenAI(
        api_key=configs["API_KEY"]
    )
    resp = client.chat.completions.create(
        model=configs["MODEL"],
        messages=messages,
        temperature=temperature,
    )
    _record_usage(getattr(resp, "usage", None))
    return resp.choices[0].message.content


def Google_chat_completion(messages, temperature):
    import google.generativeai as genai
    genai.configure(
        api_key=configs["API_KEY"]
    )
    genai.GenerationConfig(temperature=temperature)
    system_instruction = messages[0]["content"] if messages[0]["role"] == "system" else None
    messages = [item for item in messages if item["role"] != "system"]
    messages = [{"role": "model" if item["role"] == "assistant" else item["role"], "parts": item["content"]} for item in
                messages]
    history = messages[:-1]
    message = messages[-1]
    return genai.GenerativeModel(
        model_name=configs["MODEL"],
        system_instruction=system_instruction
    ).start_chat(
        history=history if history != [] else None
    ).send_message(message).text


def Anthropic_chat_completion(messages, temperature):
    import anthropic
    client = anthropic.Anthropic(
        api_key=configs["API_KEY"]
    )
    return client.messages.create(
        model=configs["MODEL"],
        messages=messages,
        temperature=temperature
    ).content


# for 3-rd party API which is compatible with OpenAI API (with different 'API_BASE')
def AI_chat_completion(messages, temperature):
    from openai import OpenAI
    client = OpenAI(
        api_key=configs["API_KEY"],
        base_url=configs["API_BASE"]
    )
    resp = client.chat.completions.create(
        model=configs["MODEL"],
        messages=messages,
        temperature=temperature,
    )
    _record_usage(getattr(resp, "usage", None))
    return resp.choices[0].message.content


def get_chat_completion(messages, temperature=0.0):
    def send_request():
        if configs["SOURCE"] == "AI":
            return AI_chat_completion(messages, temperature)
        elif configs["SOURCE"] == "OpenAI":
            return OpenAI_chat_completion(messages, temperature)
        elif configs["SOURCE"] == "Google":
            return Google_chat_completion(messages, temperature)
        elif configs["SOURCE"] == "Anthropic":
            return Anthropic_chat_completion(messages, temperature)
        else:
            raise ValueError("Invalid SOURCE in api_config file.")

    for i in range(3):
        try:
            return send_request()
        except Exception as e:
            print(e)
            if '429' in str(e):
                print("Rate limit exceeded. Waiting for 1 second.")
                time.sleep(1)
                continue
            else:
                raise e
