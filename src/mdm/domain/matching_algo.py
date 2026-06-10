import numpy as np
from sklearn.neighbors import NearestNeighbors
from src.mdm.domain.value_objects import iOSVersion


class MatchingStrategy:
    def match(self, devices, profiles) -> list:
        raise NotImplementedError


class CantiqueStrategy(MatchingStrategy):
    def __init__(self, top_k: int = 3):
        self.top_k = top_k
        self._MODEL_ENCODE = {
            "iPad Pro": 5, "iPad Air": 4, "iPad": 3, "iPad mini": 2,
            "iPhone Pro Max": 7, "iPhone Pro": 6, "iPhone Plus": 5,
            "iPhone": 4, "iPhone SE": 3,
        }
        self._TYPE_ENCODE = {"iPad": 1, "iPhone": 0}

    def _encode_device(self, d) -> list:
        model_score = self._MODEL_ENCODE.get(d.modele, 3)
        try:
            ios = iOSVersion.parse(d.ios_version)
            ios_float = ios.major + ios.minor / 10 + ios.patch / 100
        except Exception:
            ios_float = 17.0
        type_val = self._TYPE_ENCODE.get(d.type_appareil, 0.5)
        cap_norm = min(d.capacite_go / 2000, 1.0)
        util_norm = d.taux_utilisation / 100 if hasattr(d, 'taux_utilisation') else 0
        return [model_score, ios_float, type_val, cap_norm, util_norm]

    def _cantique_score(self, device_vec, profile_idx, n_profiles) -> float:
        base = 1.0 - abs(device_vec[1] - (profile_idx / n_profiles))
        compat = device_vec[0] / 7.0
        cap = 1.0 - abs(device_vec[3] - 0.3)
        return float(np.clip(base * 0.5 + compat * 0.3 + cap * 0.2, 0, 1))

    def match(self, devices, profiles):
        if not devices or not profiles:
            return []
        device_vectors = np.array([self._encode_device(d) for d in devices])
        n_profiles = max(len(profiles), 1)
        nbrs = NearestNeighbors(n_neighbors=min(self.top_k + 2, len(device_vectors)), metric="cosine", algorithm="auto")
        nbrs.fit(device_vectors)
        distances, indices = nbrs.kneighbors(device_vectors)
        results = []
        for i, device in enumerate(devices):
            candidates = set()
            for j in range(min(self.top_k, len(indices[i]))):
                neighbor_idx = indices[i][j]
                if neighbor_idx < len(profiles):
                    candidates.add(neighbor_idx)
            for j in range(len(profiles)):
                score = self._cantique_score(device_vectors[i], j, n_profiles)
                if score > 0.3:
                    candidates.add(j)
            scored = [(p, self._cantique_score(device_vectors[i], idx, n_profiles)) for idx, p in enumerate(profiles) if idx in candidates]
            scored.sort(key=lambda x: -x[1])
            for profile, score in scored[:self.top_k]:
                results.append((device, profile, float(round(score, 4))))
        return results


class KNNStrategy(MatchingStrategy):
    def __init__(self, top_k: int = 3):
        self.top_k = top_k

    def _features(self, d) -> list:
        try:
            ios = iOSVersion.parse(d.ios_version)
            ios_float = ios.major + ios.minor / 10
        except Exception:
            ios_float = 17.0
        return [float(d.capacite_go), ios_float]

    def match(self, devices, profiles):
        if not devices or not profiles:
            return []
        X = np.array([self._features(d) for d in devices])
        nbrs = NearestNeighbors(n_neighbors=min(self.top_k, len(X)), metric="euclidean")
        nbrs.fit(X)
        results = []
        for i, device in enumerate(devices):
            dists, idxs = nbrs.kneighbors([self._features(device)])
            for j in range(min(self.top_k, len(idxs[0]))):
                pid = idxs[0][j] % len(profiles)
                score = float(np.clip(1.0 / (1.0 + dists[0][j]), 0, 1))
                results.append((device, profiles[pid], round(score, 4)))
        return results


class ProfileMatcher:
    def __init__(self, strategy: MatchingStrategy = None):
        self.strategy = strategy or CantiqueStrategy(top_k=3)

    def set_strategy(self, strategy: MatchingStrategy):
        self.strategy = strategy

    def match(self, devices, profiles) -> list:
        return self.strategy.match(devices, profiles)
