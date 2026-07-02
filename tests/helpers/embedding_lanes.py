"""Shared fakes for embedding-lane tests."""


class FakeEmbedder:
    def __init__(self, dim, model, url):
        self.dim = dim
        self.model = model
        self.url = url

    def get_sentence_embedding_dimension(self):
        return self.dim

    def encode(self, texts, normalize_embeddings=True):
        return [[float(i + 1)] * self.dim for i, _ in enumerate(texts)]


class FailingEmbedder(FakeEmbedder):
    def encode(self, texts, normalize_embeddings=True):
        raise RuntimeError("embedding endpoint rate limited")


class FakeCollection:
    def __init__(self, name, metadata=None):
        self.name = name
        self.metadata = metadata or {}
        self.rows = {}
        self.dim = None

    def count(self):
        return len(self.rows)

    def add(self, ids, embeddings, documents=None, metadatas=None):
        self._check_dim(embeddings)
        documents = documents or [None] * len(ids)
        metadatas = metadatas or [{}] * len(ids)
        for row_id, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            self.rows[row_id] = {"embedding": emb, "document": doc, "metadata": meta}

    def upsert(self, ids, embeddings, documents=None, metadatas=None):
        self.add(ids, embeddings, documents=documents, metadatas=metadatas)

    def get(self, ids=None, include=None, where=None, limit=None):
        selected = list(self.rows.items())
        if ids is not None:
            id_set = set(ids)
            selected = [(row_id, row) for row_id, row in selected if row_id in id_set]
        if where:
            selected = [
                (row_id, row)
                for row_id, row in selected
                if all(row["metadata"].get(k) == v for k, v in where.items())
            ]
        if limit is not None:
            selected = selected[:limit]
        return {
            "ids": [row_id for row_id, _ in selected],
            "documents": [row["document"] for _, row in selected],
            "metadatas": [row["metadata"] for _, row in selected],
            "embeddings": [row["embedding"] for _, row in selected],
        }

    def query(self, query_embeddings, n_results, where=None, include=None):
        self._check_dim(query_embeddings)
        rows = self.get(where=where)
        ids = rows["ids"][:n_results]
        docs = rows["documents"][:n_results]
        metas = rows["metadatas"][:n_results]
        return {
            "ids": [ids],
            "documents": [docs],
            "metadatas": [metas],
            "distances": [[0.1 + i * 0.01 for i in range(len(ids))]],
        }

    def delete(self, ids):
        for row_id in ids:
            self.rows.pop(row_id, None)

    def _check_dim(self, embeddings):
        if not embeddings:
            return
        dim = len(embeddings[0])
        if self.dim is None:
            self.dim = dim
        elif self.dim != dim:
            raise RuntimeError(f"Collection expecting embedding with dimension of {self.dim}, got {dim}")


class FakeChroma:
    def __init__(self):
        self.collections = {}
        self.deleted = []
        self.fail_next_add_for = {}

    def get_or_create_collection(self, name, metadata=None):
        if name not in self.collections:
            self.collections[name] = FakeCollection(name, metadata=metadata)
            if self.fail_next_add_for.get(name, 0) > 0:
                original_add = self.collections[name].add

                def fail_once(*args, **kwargs):
                    self.fail_next_add_for[name] -= 1
                    self.collections[name].add = original_add
                    raise RuntimeError("chroma write failed")

                self.collections[name].add = fail_once
        elif metadata is not None:
            self.collections[name].metadata = metadata
        return self.collections[name]

    def get_collection(self, name):
        if name not in self.collections:
            raise KeyError(name)
        return self.collections[name]

    def delete_collection(self, name):
        self.deleted.append(name)
        self.collections.pop(name, None)


def patch_chroma(monkeypatch, fake):
    import src.chroma_client as chroma_client

    monkeypatch.setattr(chroma_client, "get_chroma_client", lambda: fake)
