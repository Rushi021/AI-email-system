"""Generic policy-document store: ingest ANY PDF, chunk it into rule-sized
pieces, index with TF-IDF, retrieve the most relevant chunks for a query.

Nothing in this file knows anything about any specific company or policy —
swap data/policy.pdf for a different company's document and it works unchanged.
"""

from __future__ import annotations

import re

from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class PolicyStore:
    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.chunks = self._load_chunks(pdf_path)
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        self.matrix = self.vectorizer.fit_transform(self.chunks)

    @staticmethod
    def _load_chunks(pdf_path: str) -> list[str]:
        reader = PdfReader(pdf_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)

        # Split on numbered-rule headings (e.g. "R1.1 ...", "3.2 ...", "Section 4").
        # Falls back to paragraph chunks for documents without numbered rules.
        pattern = re.compile(r"\n(?=(?:[A-Z]?\d+(?:\.\d+)?\s)|(?:Section\s+\d+))")
        parts = [p.strip() for p in pattern.split(text) if len(p.strip()) > 40]
        if len(parts) < 3:  # unstructured document -> paragraph chunking
            parts = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 40]
        return parts

    def retrieve(self, query: str, k: int = 3) -> list[str]:
        """Return the k chunks most similar to the query by TF-IDF cosine."""
        query_vec = self.vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self.matrix)[0]
        top = scores.argsort()[::-1][:k]
        return [self.chunks[i] for i in top]

    def all_text(self) -> str:
        return "\n\n".join(self.chunks)
