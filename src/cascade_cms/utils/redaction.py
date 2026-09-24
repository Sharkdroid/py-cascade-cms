"""Credential masking for anything that could surface the API bearer token.

Not re-exported from `cascade_cms/__init__.py` (deliberate — import from
`cascade_cms.utils.redaction` directly), consistent with `failures.py`.
"""


def mask_token(token: str) -> str:
    """Return `"****"` plus the last 4 characters of `token`.

    A token of 4 or fewer characters is masked completely (all `*`), so the
    masked form never reveals a short token in full.
    """
    if len(token) <= 4:
        return "*" * len(token)
    return "****" + token[-4:]
