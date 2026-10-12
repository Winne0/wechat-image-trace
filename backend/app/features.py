"""图像指纹与特征提取。

采用三层特征组合，兼顾「速度」与「抗编辑能力」：
1. dHash / pHash —— 全局感知哈希，抗缩放、压缩、轻度调色，用于毫秒级粗筛；
2. HSV 颜色直方图 —— 补充色彩分布信息，弥补哈希对色彩丢失的盲区；
3. ORB 局部特征 —— 关键点描述子，用于候选集的几何一致性精排（抗裁剪、加文字、拼接）。

所有特征均可序列化进 SQLite，检索时整体载入内存做向量化比对。
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image, ImageOps

# ORB 最多保留的关键点数，足够精排又不至于让 BLOB 过大
ORB_MAX_FEATURES = 400
RESIZE_EDGE = 512          # 统一长边，保证不同尺寸图片的哈希可比


def load_image(data: bytes) -> np.ndarray:
    """把任意图片字节解码为 OpenCV BGR 数组，顺带处理 EXIF 旋转与灰度图。"""
    pil = Image.open(io.BytesIO(data))
    pil = ImageOps.exif_transpose(pil)
    if pil.mode not in ("RGB", "L"):
        pil = pil.convert("RGB")
    arr = np.asarray(pil)
    if arr.ndim == 2:                       # 灰度图 -> 伪 BGR
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _normalize(bgr: np.ndarray) -> np.ndarray:
    """按长边等比缩放，消除分辨率差异对哈希的影响。"""
    h, w = bgr.shape[:2]
    scale = RESIZE_EDGE / max(h, w)
    if scale < 1.0:
        bgr = cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    return bgr


def dhash(bgr: np.ndarray) -> int:
    """差值哈希：比较水平相邻像素，64 位整数。"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    diff = gray[:, 1:] > gray[:, :-1]
    return int(_bits_to_int(diff.flatten()))


def phash(bgr: np.ndarray) -> int:
    """感知哈希：DCT 低频能量分布，对缩放/压缩最稳健。"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    gray = np.float32(gray)
    dct = cv2.dct(gray)
    low = dct[:8, :8].flatten()
    low[0] = 0                              # 去掉直流分量（受亮度影响大）
    median = np.median(low)
    return int(_bits_to_int(low > median))


def _bits_to_int(bits: np.ndarray) -> int:
    value = 0
    for bit in bits:
        value = (value << 1) | int(bool(bit))
    return value


def histogram(bgr: np.ndarray, bins: tuple = (12, 12, 12)) -> np.ndarray:
    """HSV 三维直方图，归一化后作余弦相似度。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, bins,
                        [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
    return hist.flatten().astype(np.float32)


def orb(bgr: np.ndarray) -> tuple:
    """ORB 关键点与描述子。

    返回 (描述子 (N,32) uint8, 关键点 (N,5) float32)，
    关键点列为 x, y, size, angle, response —— 几何验证需要坐标。
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    detector = cv2.ORB_create(nfeatures=ORB_MAX_FEATURES)
    kps, desc = detector.detectAndCompute(gray, None)
    if desc is None or len(kps) == 0:
        return np.zeros((0, 32), np.uint8), np.zeros((0, 5), np.float32)
    arr = np.array([[k.pt[0], k.pt[1], k.size, k.angle, k.response]
                    for k in kps], dtype=np.float32)
    return desc, arr


def extract(data: bytes) -> dict:
    """一次性提取全部特征。"""
    bgr = _normalize(load_image(data))
    desc, kps = orb(bgr)
    return {
        "dhash": dhash(bgr),
        "phash": phash(bgr),
        "hist": histogram(bgr),
        "orb": desc,
        "kp": kps,
        "width": int(bgr.shape[1]),
        "height": int(bgr.shape[0]),
    }


# --------------------------------------------------------------------------
# 序列化：SQLite 里存 BLOB / 十六进制字符串
# --------------------------------------------------------------------------

def pack_hash(value: int) -> str:
    return f"{value & 0xFFFFFFFFFFFFFFFF:016x}"


def unpack_hash(text: str) -> int:
    return int(text, 16)


def pack_hist(hist: np.ndarray) -> bytes:
    return hist.astype(np.float32).tobytes()


def unpack_hist(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def pack_orb(desc: np.ndarray) -> bytes:
    if desc is None or len(desc) == 0:
        return b""
    return desc.astype(np.uint8).tobytes()


def unpack_orb(blob: bytes) -> np.ndarray:
    if not blob:
        return np.zeros((0, 32), np.uint8)
    arr = np.frombuffer(blob, dtype=np.uint8)
    return arr.reshape(-1, 32)


def pack_kp(kps: np.ndarray) -> bytes:
    if kps is None or len(kps) == 0:
        return b""
    return kps.astype(np.float32).tobytes()


def unpack_kp(blob: bytes) -> np.ndarray:
    if not blob:
        return np.zeros((0, 5), np.float32)
    arr = np.frombuffer(blob, dtype=np.float32)
    return arr.reshape(-1, 5)


# --------------------------------------------------------------------------
# 相似度
# --------------------------------------------------------------------------

def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")


def hamming_score(a: int, b: int) -> float:
    """汉明距离 -> [0,1] 相似度。64 位哈希，距离 10 以内通常已是同源图。"""
    return 1.0 - hamming(a, b) / 64.0


def hist_score(a: np.ndarray, b: np.ndarray) -> float:
    """直方图余弦相似度，落在 [0,1]。"""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.clip(np.dot(a, b) / denom, 0.0, 1.0))


def orb_match(q_desc: np.ndarray, t_desc: np.ndarray) -> list:
    """BFMatcher + Lowe 比值检验，返回通过筛选的匹配对。"""
    if q_desc is None or t_desc is None or len(q_desc) == 0 or len(t_desc) == 0:
        return []
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    try:
        raw = matcher.knnMatch(q_desc, t_desc, k=2)
    except cv2.error:
        return []
    good = []
    for pair in raw:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:   # Lowe's ratio test
            good.append(m)
    return good


def orb_score(q_desc, q_kp, t_desc, t_kp) -> tuple:
    """局部特征匹配 + 几何校验。

    返回 (得分, 内点数, 粗匹配数)。
    光靠描述子匹配会有大量散乱误匹配（尤其纹理相似的图），
    这里用 RANSAC 估计单应变换，只保留空间关系自洽的内点。
    """
    good = orb_match(q_desc, t_desc)
    if not good:
        return 0.0, 0, 0

    # 关键点不足时无法做几何验证，给一个打折的弱信号
    if len(good) < 8 or q_kp is None or t_kp is None \
            or len(q_kp) == 0 or len(t_kp) == 0:
        weak = float(min(1.0, np.log1p(len(good)) / np.log1p(60))) * 0.45
        return weak, 0, len(good)

    src = np.float32([q_kp[m.queryIdx][:2] for m in good]).reshape(-1, 1, 2)
    dst = np.float32([t_kp[m.trainIdx][:2] for m in good]).reshape(-1, 1, 2)
    try:
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    except cv2.error:
        return 0.0, 0, len(good)
    if mask is None:
        return 0.0, 0, len(good)

    inliers = int(mask.ravel().sum())
    if inliers == 0:
        return 0.0, 0, len(good)

    ratio = inliers / len(good)                       # 内点占比，衡量一致性
    base = float(min(1.0, inliers / 30.0))
    score = base * (0.35 + 0.65 * ratio)
    return float(score), inliers, len(good)
