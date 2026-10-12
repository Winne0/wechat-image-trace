"""SQLite 数据层：文章 + 图片指纹索引。"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (Column, DateTime, Float, ForeignKey, Integer,
                        LargeBinary, String, Text, create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGE_DIR = os.path.join(DATA_DIR, "images")
os.makedirs(IMAGE_DIR, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'index.db')}")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True)
    url = Column(String(1024), unique=True, nullable=False, index=True)
    title = Column(String(512), default="")
    account = Column(String(256), default="")     # 公众号名称
    publish_time = Column(String(64), default="")
    cover = Column(String(1024), default="")
    status = Column(String(32), default="pending")  # pending | ok | failed
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    images = relationship("Image", back_populates="article",
                          cascade="all, delete-orphan")


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    src_url = Column(String(1024), nullable=False)
    local_path = Column(String(512), default="")   # 缓存的缩略图，用于回显
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    # 指纹
    dhash = Column(String(16), default="")
    phash = Column(String(16), default="")
    hist = Column(LargeBinary, default=b"")
    orb = Column(LargeBinary, default=b"")
    kp = Column(LargeBinary, default=b"")      # ORB 关键点坐标，用于几何校验
    # 元信息
    alt = Column(String(512), default="")
    position = Column(Integer, default=0)          # 在正文中的顺序
    is_cover = Column(Integer, default=0)
    status = Column(String(32), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

    article = relationship("Article", back_populates="images")


Base.metadata.create_all(engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
