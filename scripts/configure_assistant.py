"""
Solrise ERP - configure the LLM assistant ("Ask Solrise") on a site.

`Solrise Settings` is the one piece of the deployment that has no script: the
provider, the key and the system prompt live in a Single DocType, so until now
the only way to set them was to type them into the Desk or to paste a `bench
console` snippet. This is the supported path - the same shape as
`configure_s3_media.py` (settings) and `configure_email.py` (credentials): read
the environment, write what changed, prove it works, never print the secret.

Normally run for the live site over SSH or SSM:
    ASSISTANT_API_KEY='sk-...' SITE_ENV=aws ./scripts/run-python.sh \\
        scripts/configure_assistant.py
or on the workstation with `make assistant`.

Inputs come from the environment (nothing is read from git or Vault):
    ASSISTANT_ENABLED             auto (default) | 1 | 0
                                  auto = on when a key is given, or when the
                                  provider needs none (Ollama)
    ASSISTANT_PROVIDER            OpenAI (default) | Ollama | Azure OpenAI | Custom
    ASSISTANT_API_BASE_URL        defaults to the provider's public endpoint
    ASSISTANT_API_KEY             the secret; stored encrypted by Frappe
    ASSISTANT_MODEL               default gpt-4o-mini
    ASSISTANT_MAX_TOKENS          default 1024
    ASSISTANT_TEMPERATURE         default 0.2
    ASSISTANT_RATE_LIMIT_PER_HOUR default 60
    ASSISTANT_SYSTEM_PROMPT       default: the prompt in docs/06 section 4.2
    ASSISTANT_CHAT_FALLBACK       1 | 0 - the universal chat's LLM slot-filler;
                                  left alone when unset (its safe default is off)
    ASSISTANT_PROBE               1 (default) | 0 - send one real message and
                                  report the reply; set 0 to configure offline

Safe to re-run: every field is written only when it differs, the key is compared
after decryption, and an unchanged run writes nothing. The key never appears in
the output - only whether it is set and its length.
"""
import os
import sys

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = "/home/frappe/frappe-bench/sites"
DOCTYPE = "Solrise Settings"

TRUE = ("1", "true", "yes", "on")
FALSE = ("0", "false", "no", "off")

# Providers that answer without a key. Everything else needs ASSISTANT_API_KEY.
NO_KEY_PROVIDERS = ("Ollama",)

DEFAULT_BASE_URLS = {
    "OpenAI": "https://api.openai.com/v1",
}

# The prompt docs/06 section 4.2 shows. Applied only when the field is empty, so
# an operator's later edit is never overwritten.
DEFAULT_SYSTEM_PROMPT = (
    "You are the Solrise ERP assistant. Only use the provided tools. Never "
    "invent record names. Ask a clarifying question when the request is "
    "ambiguous."
)


def env(name, default=""):
    return os.environ.get(name, default).strip()


def env_flag(name, default):
    """Tri-state: unset -> `default`; otherwise parse a boolean flag."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    raw = raw.strip().lower()
    if raw in TRUE:
        return True
    if raw in FALSE:
        return False
    return default


def env_int(name, default):
    try:
        return int(env(name) or default)
    except ValueError:
        return default


def env_float(name, default):
    try:
        return float(env(name) or default)
    except ValueError:
        return default


def wanted_key():
    return env("ASSISTANT_API_KEY")


def desired_enabled(provider, key):
    """`ASSISTANT_ENABLED` wins; `auto` turns on only when the chat can answer."""
    raw = env("ASSISTANT_ENABLED", "auto").lower()
    if raw in TRUE:
        return True
    if raw in FALSE:
        return False
    return bool(key) or provider in NO_KEY_PROVIDERS


def changed(doc, fieldname, value):
    """True when `fieldname` is being set to something new."""
    return doc.get(fieldname) != value


def apply(doc, fieldname, value, updates):
    if changed(doc, fieldname, value):
        updates[fieldname] = value


def main():
    provider = env("ASSISTANT_PROVIDER", "OpenAI")
    key = wanted_key()
    base_url = env("ASSISTANT_API_BASE_URL") or DEFAULT_BASE_URLS.get(provider, "")
    enabled = desired_enabled(provider, key)

    if enabled and not key and provider not in NO_KEY_PROVIDERS:
        print(
            "refusing to enable: provider {0} needs ASSISTANT_API_KEY "
            "(set ASSISTANT_ENABLED=0, or use Ollama which needs none)".format(provider)
        )
        return 1

    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    try:
        doc = frappe.get_doc(DOCTYPE)
        updates = {}

        apply(doc, "provider", provider, updates)
        if base_url:
            apply(doc, "api_base_url", base_url, updates)
        apply(doc, "model", env("ASSISTANT_MODEL", "gpt-4o-mini"), updates)
        apply(doc, "max_tokens", env_int("ASSISTANT_MAX_TOKENS", 1024), updates)
        apply(doc, "temperature", env_float("ASSISTANT_TEMPERATURE", 0.2), updates)
        apply(
            doc,
            "rate_limit_per_hour",
            env_int("ASSISTANT_RATE_LIMIT_PER_HOUR", 60),
            updates,
        )

        prompt = env("ASSISTANT_SYSTEM_PROMPT") or (
            DEFAULT_SYSTEM_PROMPT if not (doc.get("system_prompt") or "").strip() else ""
        )
        if prompt:
            apply(doc, "system_prompt", prompt, updates)

        fallback = env_flag("ASSISTANT_CHAT_FALLBACK", None)
        if fallback is not None:
            apply(doc, "chat_enable_llm_fallback", 1 if fallback else 0, updates)

        if changed(doc, "enabled", 1 if enabled else 0):
            updates["enabled"] = 1 if enabled else 0

        # The key is a Password field: the stored value is encrypted, so compare
        # the decrypted value before touching it.
        try:
            current_key = doc.get_password("api_key", raise_exception=False) or ""
        except Exception:
            current_key = ""
        key_changed = bool(key) and current_key != key
        if key_changed:
            doc.api_key = key

        for fieldname, value in updates.items():
            doc.set(fieldname, value)
            print("  set {0} = {1}".format(fieldname, _show(fieldname, value)))
        if key_changed:
            print("  set api_key = (secret, {0} chars)".format(len(key)))

        if updates or key_changed:
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            print("Solrise Settings: updated")
        else:
            print("Solrise Settings: already configured (nothing written)")

        print(
            "assistant: enabled={0} provider={1} model={2} base_url={3} key={4}".format(
                int(enabled),
                provider,
                doc.get("model"),
                doc.get("api_base_url") or "(none)",
                "set" if (key or current_key) else "absent",
            )
        )

        if not enabled:
            print(
                "  note: the assistant is off, so the chat answers deterministically. "
                "Give ASSISTANT_API_KEY (or point at Ollama) and re-run."
            )
            return 0

        if not env_flag("ASSISTANT_PROBE", True):
            print("  probe skipped (ASSISTANT_PROBE=0)")
            return 0
        return probe()
    finally:
        frappe.destroy()


def probe():
    """One real message, so a bad key or an unreachable endpoint fails loudly."""
    from solrise_erp.assistant import engine

    print("probing the provider with one message ...")
    try:
        result = engine.ask("Reply with the single word: ready")
    except Exception as exc:  # noqa: BLE001 - the message is the report
        print("  probe FAILED: {0}: {1}".format(type(exc).__name__, exc))
        return 1
    if not result.get("ok"):
        print("  probe FAILED: {0}".format(result.get("error")))
        return 1
    print("  probe ok: reply={0!r} tokens={1}".format(
        (result.get("reply") or "")[:80], result.get("tokens")))
    return 0


def _show(fieldname, value):
    """Never print the secret, even if it is passed as a scalar field."""
    if fieldname in ("api_key",):
        return "(secret)"
    return repr(value)


if __name__ == "__main__":
    sys.exit(main())
