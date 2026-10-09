"""One request to the Anthropic API for Pick's assistant (see assistant.py).

Runs in the assistant's own environment, where the `anthropic` package is.
stdin: {"messages": [...], "model": "..."}; stdout: {"content", "stop_reason",
"usage"} or {"error": a Czech sentence}.
"""
import json
import sys

import anthropic

import assistant


def main():
    req = json.load(sys.stdin)
    client = anthropic.Anthropic(max_retries=1, timeout=45.0)
    try:
        r = client.messages.create(
            model=req.get("model") or assistant.MODEL,
            max_tokens=1024,
            # The description of the app and its tools is the same on every request: cached.
            system=[{"type": "text", "text": assistant.SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=assistant.TOOLS,
            messages=req["messages"],
        )
    except anthropic.AuthenticationError:
        return {"error": "API klíč není platný. Zkontroluj ho v Claude Console."}
    except anthropic.PermissionDeniedError:
        return {"error": "API klíč nemá oprávnění tenhle model používat."}
    except anthropic.RateLimitError:
        return {"error": "Asistent je teď přetížený, zkus to za chvíli."}
    except anthropic.BadRequestError as e:
        if "credit balance" in str(e.message):
            return {"error": "Na účtu u Anthropic není kredit. Dobij ho v Claude Console (Plans & Billing) a zkus to znovu."}
        return {"error": "Asistent požadavek odmítl: " + str(e.message)[:200]}
    except anthropic.APIStatusError as e:
        return {"error": f"Chyba služby ({e.status_code}). Zkus to znovu."}
    except anthropic.APIConnectionError:
        return {"error": "Nejde se připojit k internetu."}
    if r.stop_reason == "refusal":
        return {"error": "Tohle asistent udělat odmítl."}
    content = []
    for b in r.content:
        if b.type == "text":
            content.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    u = r.usage
    return {"content": content, "stop_reason": r.stop_reason,
            "usage": {"input": u.input_tokens, "output": u.output_tokens,
                      "cache_read": u.cache_read_input_tokens or 0, "cache_write": u.cache_creation_input_tokens or 0}}


if __name__ == "__main__":
    json.dump(main(), sys.stdout)
