"""Make Python trust the OS certificate store.

On machines behind a TLS-inspecting proxy (corporate networks: Zscaler, Netskope, etc.),
Python's default CA bundle doesn't include the proxy's root CA, so HTTPS to Google/Anthropic
fails with CERTIFICATE_VERIFY_FAILED. The browser works because it uses the OS trust store,
which *does* have that CA. `truststore` routes Python's TLS verification through that same OS
store. On machines without such a proxy (e.g. CI runners) this is a harmless no-op.

Call `use_system_trust_store()` once, early, at each entry point (before any HTTPS call).
"""

from __future__ import annotations


def use_system_trust_store() -> None:
    """Best-effort: route TLS verification through the OS trust store. No-op if unavailable."""
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()
