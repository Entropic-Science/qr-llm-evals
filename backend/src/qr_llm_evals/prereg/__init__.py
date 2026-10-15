"""Pre-registration Markdown and D-11 byte hashes; imports perform no I/O."""

from qr_llm_evals.prereg.hashes import config_hash, prereg_hash
from qr_llm_evals.prereg.render import Renderer, load_renderer, render

__all__ = ["Renderer", "config_hash", "load_renderer", "prereg_hash", "render"]
