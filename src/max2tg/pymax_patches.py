"""
Patches for pymax that haven't been merged upstream yet.

All previously patched issues are now fixed directly in dimonb/PyMax:
  - asyncio.Lock around _send_and_wait  (fixed via _sock_lock in socket.py)
  - clean connect() cancelling old tasks (fixed in socket.py)
  - SSL context: TLS 1.2 only, no bad set_ciphers("DEFAULT") (fixed in core.py)
  - ReplyLink.message_id int vs str     (fixed in payloads.py + message.py)

This module is kept as an extension point for future patches.
"""

from __future__ import annotations


def apply() -> None:
    """Apply all active patches. Call once before any other pymax import."""
    pass  # nothing to patch currently
