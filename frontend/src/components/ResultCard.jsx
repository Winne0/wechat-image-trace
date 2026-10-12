import { useState } from 'react'

const CONF_TEXT = { high: '高置信', medium: '疑似匹配', low: '弱相关' }

export default function ResultCard({ hit, queryPreview, queryImageUrl }) {
  const [failed, setFailed] = useState(false)
  const querySrc = queryPreview
    || (queryImageUrl ? `/api/proxy-image?url=${encodeURIComponent(queryImageUrl)}` : '')

  const pct = (hit.score * 100).toFixed(1)
  const { article, image, evidence, confidence } = hit

  return (
    <div className="hit">
      <div>
        <img className="thumb" src={querySrc} alt="查询图" />
        <div className="thumb-label">你的图片</div>
      </div>

      <div className="arrow">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12h14m0 0-5-5m5 5-5 5" />
        </svg>
      </div>

      <div>
        {failed ? (
          <div className="thumb" style={{
            display: 'grid', placeItems: 'center', fontSize: 11, color: 'var(--dim)',
          }}>图源已失效</div>
        ) : (
          <img
            className="thumb"
            src={image.preview}
            alt={image.alt || '命中图片'}
            onError={() => setFailed(true)}
          />
        )}
        <div className="thumb-label">
          {image.is_cover ? '文章封面' : `正文第 ${image.position + 1} 张`}
        </div>
      </div>

      <div className="hit-article">
        <div className="hit-top">
          <span className={`badge ${confidence}`}>{CONF_TEXT[confidence] || confidence}</span>
          <span className="score">{pct}<small>%</small></span>
          <span style={{ fontSize: 11.5, color: 'var(--dim)' }}>相似度</span>
        </div>

        <h3 className="hit-title">{article.title}</h3>

        <div className="hit-meta">
          {article.account && <span className="acct">公众号 · {article.account}</span>}
          {article.publish_time && <span>{article.publish_time}</span>}
          {image.width ? <span>{image.width}×{image.height}</span> : null}
        </div>

        <div className="evidence">
          <span className="evi">哈希距离 {evidence.hash_distance}/64</span>
          <span className="evi">色彩相似度 {(evidence.hist_similarity * 100).toFixed(0)}%</span>
          <span className="evi">
            几何内点 {evidence.orb_inliers ?? 0}/{evidence.orb_matches}
          </span>
        </div>

        <div className="hit-actions">
          <a className="btn-link" href={article.url} target="_blank" rel="noreferrer">
            打开原文
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 17 17 7M17 7H9m8 0v8" />
            </svg>
          </a>
          <a className="btn-link" href={image.url} target="_blank" rel="noreferrer"
            style={{ background: 'rgba(255,255,255,.05)', borderColor: 'var(--border)', color: 'var(--muted)' }}>
            查看原图
          </a>
        </div>
      </div>
    </div>
  )
}
