"""检索引擎：内存常驻指纹表，粗筛 + 精排两阶段。

粗筛用 64 位感知哈希的汉明距离（向量化，万级图片也能毫秒返回）；
精排只对 Top-N 候选计算直方图余弦与 ORB 局部匹配，控制开销。
"""

from __future__ import annotations

import threading
import time

import numpy as np

from . import features
from .db import Image, SessionLocal

COARSE_TOP = 60          # 进入精排的候选数
HASH_GATE = 22           # 汉明距离粗筛闸门（64 位中差异超过 1/3 基本无望）


def _to_bit_matrix(values: np.ndarray) -> np.ndarray:
    """把 uint64 数组展开成 (N, 64) 的 0/1 矩阵，便于向量化汉明距离。"""
    if values.size == 0:
        return np.zeros((0, 64), np.uint8)
    return np.unpackbits(values.astype(">u8").view(np.uint8).reshape(-1, 8), axis=1)


class SearchIndex:
    def __init__(self):
        self._lock = threading.RLock()
        self._rows: list = []            # 与向量表同序的元数据
        self._dhash = np.zeros((0, 64), np.uint8)
        self._phash = np.zeros((0, 64), np.uint8)
        self._hist: np.ndarray | None = None
        self._orbs: list = []
        self._kps: list = []
        self._loaded_at = 0.0

    # ------------------------------------------------------------------
    def refresh(self):
        session = SessionLocal()
        try:
            rows = (session.query(Image)
                    .filter(Image.status == "ok")
                    .order_by(Image.id)
                    .all())
        finally:
            session.close()

        dhash_vals, phash_vals, hists, orbs, kps, meta = [], [], [], [], [], []
        for img in rows:
            try:
                d = features.unpack_hash(img.dhash) if img.dhash else 0
                p = features.unpack_hash(img.phash) if img.phash else 0
                h = features.unpack_hist(img.hist) if img.hist else np.zeros(1, np.float32)
            except ValueError:
                continue
            dhash_vals.append(d)
            phash_vals.append(p)
            hists.append(h)
            orbs.append(features.unpack_orb(img.orb))
            kps.append(features.unpack_kp(getattr(img, "kp", b"")))
            meta.append({
                "image_id": img.id,
                "article_id": img.article_id,
                "src_url": img.src_url,
                "local_path": img.local_path,
                "width": img.width,
                "height": img.height,
                "alt": img.alt,
                "position": img.position,
                "is_cover": img.is_cover,
                "dhash": d,
                "phash": p,
            })

        with self._lock:
            self._rows = meta
            self._dhash = _to_bit_matrix(np.array(dhash_vals, dtype=np.uint64))
            self._phash = _to_bit_matrix(np.array(phash_vals, dtype=np.uint64))
            self._hist = np.vstack(hists) if hists else np.zeros((0, 1), np.float32)
            self._orbs = orbs
            self._kps = kps
            self._loaded_at = time.time()
        return len(meta)

    def ensure_loaded(self, max_age: float = 30.0):
        """索引有变更时（导入后）自动重载，最多 30 秒缓存。"""
        if time.time() - self._loaded_at > max_age or not self._rows:
            self.refresh()

    @property
    def size(self) -> int:
        return len(self._rows)

    # ------------------------------------------------------------------
    def search(self, feats: dict, top_k: int = 12) -> list:
        self.ensure_loaded()
        if not self._rows:
            return []

        qd = np.unpackbits(np.array([feats["dhash"]], dtype=">u8").view(np.uint8))
        qp = np.unpackbits(np.array([feats["phash"]], dtype=">u8").view(np.uint8))
        qh = feats["hist"].astype(np.float32)

        with self._lock:
            dist_d = (self._dhash ^ qd).sum(axis=1)
            dist_p = (self._phash ^ qp).sum(axis=1)
            dist = np.minimum(dist_d, dist_p)

            # 粗筛：先取距离最小的若干候选
            candidates = np.argsort(dist)[:COARSE_TOP]
            if dist.size and dist[candidates[0]] > HASH_GATE:
                # 连最接近的都差得太远，仍保留少量候选避免误杀（如大幅裁剪）
                candidates = np.argsort(dist)[:10]

            hist_mat = self._hist[candidates]
            norm_q = np.linalg.norm(qh)
            norm_t = np.linalg.norm(hist_mat, axis=1)
            denom = norm_t * norm_q
            hist_sim = np.where(denom > 0,
                                hist_mat @ qh / np.maximum(denom, 1e-8), 0.0)
            hist_sim = np.clip(hist_sim, 0.0, 1.0)

            qdesc = feats["orb"]
            qkp = feats.get("kp")
            results = []
            for j, idx in enumerate(candidates):
                meta = self._rows[idx]
                orb_s, inliers, matched = features.orb_score(
                    qdesc, qkp, self._orbs[idx], self._kps[idx])
                d_score = max(1.0 - dist_d[idx] / 64.0, 1.0 - dist_p[idx] / 64.0)
                h_score = float(hist_sim[j])

                # 三层加权：几何校验后的局部特征最能证明「同一张图」
                score = 0.30 * d_score + 0.12 * h_score + 0.58 * orb_s

                # 一致性惩罚：同一张图被二次传播，色彩分布不该面目全非。
                # 色彩对不上却能匹配到大量特征点，多半是纹理巧合，降权处理。
                if h_score < 0.35:
                    score *= 0.70
                elif h_score < 0.60:
                    score *= 0.88

                # 强信号加成：哈希接近一致且几何内点足够多
                if min(dist_d[idx], dist_p[idx]) <= 6 and inliers >= 15:
                    score = min(1.0, score + 0.10)

                results.append({
                    **meta,
                    "score": float(score),
                    "hash_distance": int(min(dist_d[idx], dist_p[idx])),
                    "hist_similarity": h_score,
                    "orb_matches": int(matched),
                    "orb_inliers": int(inliers),
                })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]


index = SearchIndex()
