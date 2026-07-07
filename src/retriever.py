"""TF-IDF retrieval over the past-ticket corpus: given a new incoming email,
find the most similar historical (email, actual_reply) pairs.

Generic: works on any list of Ticket objects regardless of company/domain.
"""

from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.schema import Ticket


class TicketRetriever:
    def __init__(self, tickets: list[Ticket]):
        """Fit on the corpus split only — holdout tickets must never be
        retrievable, or the test set would leak into generation."""
        self.tickets = [t for t in tickets if t.split == "corpus"]
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        self.matrix = self.vectorizer.fit_transform(
            [t.incoming_email for t in self.tickets]
        )

    def top_k(self, text: str, k: int = 3) -> list[Ticket]:
        query_vec = self.vectorizer.transform([text])
        scores = cosine_similarity(query_vec, self.matrix)[0]
        top = scores.argsort()[::-1][:k]
        return [self.tickets[i] for i in top]
