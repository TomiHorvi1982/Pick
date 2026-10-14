"""One request to the Anthropic API for Skladatel's lyricist (see lyricist.py).

Runs in the assistant's own environment, where the `anthropic` package is.
stdin: {"system", "prompt", "model"}; stdout: {"text"} or {"error": a Czech sentence}.
"""
import json
import sys

import anthropic


def main():
    req = json.load(sys.stdin)
    client = anthropic.Anthropic(max_retries=1, timeout=220.0)
    try:
        r = client.messages.create(
            model=req["model"],
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=req["system"],
            messages=[{"role": "user", "content": req["prompt"]}],
        )
    except anthropic.AuthenticationError:
        return {"error": "API klíč není platný. Zkontroluj ho v Claude Console."}
    except anthropic.PermissionDeniedError:
        return {"error": "API klíč nemá oprávnění tenhle model používat."}
    except anthropic.RateLimitError:
        return {"error": "Služba je teď přetížená, zkus to za chvíli."}
    except anthropic.BadRequestError as e:
        if "credit balance" in str(e.message):
            return {"error": "Na účtu u Anthropic není kredit. Dobij ho v Claude Console (Plans & Billing)."}
        return {"error": "Požadavek byl odmítnut: " + str(e.message)[:200]}
    except anthropic.APIStatusError as e:
        return {"error": f"Chyba služby ({e.status_code}). Zkus to znovu."}
    except anthropic.APIConnectionError:
        return {"error": "Nejde se připojit k internetu."}
    if r.stop_reason == "refusal":
        return {"error": "Tenhle text psát odmítl."}
    return {"text": "".join(b.text for b in r.content if b.type == "text")}


if __name__ == "__main__":
    json.dump(main(), sys.stdout)
