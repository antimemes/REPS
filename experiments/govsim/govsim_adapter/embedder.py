"""Embedders for GovSim's associative memory.

``hash`` (the keyless default): a deterministic sha256 → unit-vector embedder,
duck-typing upstream's ``EmbeddingModel`` (``embed`` / ``embed_retrieve``).
Declared deviation from the paper: memory *relevance* becomes content-hash
noise. Thus retrieval ranking is driven by recency (0.99^i, w=0.5) and
LLM-rated importance (w=3); ``always_include`` nodes (agreed limits) are
unaffected.

``mxbai``: the paper's ``mixedbread-ai/mxbai-embed-large-v1``, pinned below, through
sentence-transformers — must have network (HF download, ~1.3 GB) or a warm
``HF_HOME``; not the prefilled path.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1/commit/b33106f585b9ce46904ad7443a3b52b7a63e231c
MXBAI_MODEL = "mixedbread-ai/mxbai-embed-large-v1"
MXBAI_REVISION = "b33106f585b9ce46904ad7443a3b52b7a63e231c"

# upstream's dimension (mxbai-embed-large-v1); ndarrays are necessary
# because embeddings land in embeddings.json through upstream's NumpyEncoder
_DIM = 1024
_RETRIEVE_PREFIX = "Represent this sentence for searching relevant passages: "


class HashEmbedder:
    """Deterministic, offline, nonzero-norm (cosine similarity divides by it)."""

    def embed(self, text: str) -> np.ndarray:
        import numpy as np

        digest = hashlib.sha256(text.encode("utf-8")).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        vec = rng.standard_normal(_DIM)
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    def embed_retrieve(self, text: str) -> np.ndarray:
        # upstream's query prefix is kept; with it the hash space at least
        # separates queries from passages the way mxbai's prompt does
        return self.embed(f"{_RETRIEVE_PREFIX}{text}")


def make_embedder(kind: str):
    if kind == "hash":
        return HashEmbedder()
    if kind == "mxbai":
        # Keep upstream's encoding and retrieval prefix; only pin construction.
        from simulation.persona import EmbeddingModel
        from sentence_transformers import SentenceTransformer

        class PinnedEmbeddingModel(EmbeddingModel):
            def __init__(self):
                self.device = "cpu"
                self.model = SentenceTransformer(
                    MXBAI_MODEL, device=self.device, revision=MXBAI_REVISION,
                )

        return PinnedEmbeddingModel()
    raise ValueError(f"unknown embedder: {kind!r} (expected 'hash' or 'mxbai')")
