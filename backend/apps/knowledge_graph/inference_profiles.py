"""Responsibility: Declare explicit semantic-graph inference profiles and cacheable prompt ordering.
Implementation: Preserve baseline parameters; other profiles independently vary context, attention, KV precision, or shared-prefix ordering.
Relationships: Shared by run_graph_model, semantic_provider, and local comparison tools; training data and fact validation remain unchanged.
Directory:
- get_profile: Validate an explicit profile name and return an independent configuration.
- prepare_messages: Preserve all content while placing stable schema before dynamic time fields.
- server_arguments: Generate fixed model-profile arguments.
Variable index:
- PROFILES: Initial six profiles plus the supplementary prefix8 deployment comparison; baseline is the sole default.
"""
import copy
import json

PROFILES = {
    "baseline": {"context": 16384, "kv": "f16", "flash": "auto", "cache": False, "prompt_layout": "legacy"},
    "context8": {"context": 8192, "kv": "f16", "flash": "auto", "cache": False, "prompt_layout": "legacy"},
    "flash8": {"context": 8192, "kv": "f16", "flash": "on", "cache": False, "prompt_layout": "legacy"},
    "kv8": {"context": 8192, "kv": "q8_0", "flash": "on", "cache": False, "prompt_layout": "legacy"},
    "prefix16": {"context": 16384, "kv": "f16", "flash": "auto", "cache": True, "prompt_layout": "schema-first-v1"},
    "combined8": {"context": 8192, "kv": "q8_0", "flash": "on", "cache": True, "prompt_layout": "schema-first-v1"},
    "prefix8": {"context": 8192, "kv": "f16", "flash": "auto", "cache": True, "prompt_layout": "schema-first-v1"},
}


# Function: Read an explicitly named inference profile.
# Inputs: `name`: exact name in PROFILES.
# Outputs: Independent configuration dictionary; unknown names raise ValueError.
# Logic: Deep-copy to prevent callers from contaminating other requests or fixed experiment profiles.
# Constraints: Do not select fallback profiles or adjust parameters automatically.
def get_profile(name):
    if name not in PROFILES:
        raise ValueError("Unknown graph inference profile")
    return copy.deepcopy(PROFILES[name])


# Function: Prepare complete messages with a reusable shared prefix.
# Inputs: `prompt`: existing system/user messages; `profile`: validated configuration dictionary.
# Outputs: Deep-copy messages; schema-first profiles only reorder JSON keys.
# Logic: Preserve every schema, candidate, fact, timestamp, and new text; place stable schema before candidates and time.
# Constraints: Do not truncate, filter, or treat existing facts as new evidence; explicitly reject mismatched protocol shapes.
def prepare_messages(prompt, profile):
    result = copy.deepcopy(prompt)
    if profile["prompt_layout"] == "legacy":
        return result
    if len(result) != 2 or result[0]["role"] != "system" or result[1]["role"] != "user":
        raise ValueError("Unexpected semantic message envelope")
    content = json.loads(result[1]["content"])
    if set(content) != {"observed_at", "existing_context", "new_text"}:
        raise ValueError("Unexpected semantic context envelope")
    context = content["existing_context"]
    if set(context) != {"schema", "entities", "facts", "generation"}:
        raise ValueError("Unexpected semantic context fields")
    ordered = {"existing_context": {"schema": context["schema"], "entities": context["entities"],
               "facts": context["facts"], "generation": context["generation"]},
               "observed_at": content["observed_at"], "new_text": content["new_text"]}
    result[1]["content"] = json.dumps(ordered, ensure_ascii=False)
    return result


# Function: Construct configuration arguments for a single-slot CPU model.
# Inputs: `name`: declared profile name; `threads`: positive integer CPU thread count.
# Outputs: Argument list for llama-server.
# Logic: Set context, KV type, Flash Attention, and caching explicitly by profile, retaining disabled fit and sliding behavior.
# Constraints: The default baseline adds no low-precision KV; caching reuses only identical token prefixes without inserting previous requests.
def server_arguments(name, threads):
    profile = get_profile(name)
    if type(threads) is not int or threads < 1:
        raise ValueError("threads must be a positive integer")
    return ["-ngl", "0", "-t", str(threads), "-tb", str(threads), "-c", str(profile["context"]),
            "-np", "1", "--fit", "off", "-fa", profile["flash"], "-ctk", profile["kv"], "-ctv", profile["kv"],
            "--cache-ram", "0", "--cache-prompt" if profile["cache"] else "--no-cache-prompt",
            "--no-context-shift", "--offline"]
