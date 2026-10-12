import { useState } from 'react'

const CONF_TEXT = { high: '高置信', medium: '疑似匹配', low: '弱相关' }
const R = 32                                  // 得分环半径
const C = 2 * Math.PI * R                     // 周长

function Ring({ score, color }) {
  const dash = C * Math.max(0, Math.min(1, score))
  return (
    <div className="ring">
      <svg width="76" height="76" viewBox="0 0 76 76">
        <circle cx="38" cy="38" r={R} fill="none"
          stroke="rgba(226,220,208,.09)" strokeWidth="5" />
        <circle cx="38" cy="38" r={R} fill="none"
          stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={`${dash} ${C}`} />
      </svg>
      <div className="ring-val">
        {(score * 100).toFixed(1)}<small>%</small>
      </div>
    </div>
  )
}

function Bar({ label, value, text }) {
  const v = Math.max(0, Math.min(1, value))
  return (
    <div className="evi-row">
      <label>{label}</label>
      <div className={`evi-bar ${v >= 0.7 ? 'g' : 'a'}`}>
        <i style={{ width: `${v * 100}%` }} />
      </div>
      <span>{text}</span>
    </div>
  )
}

export default function ResultCard({ hit, queryPreview, queryImageUrl, rank = 1 }) {
  const [failed, setFailed] = useState(false)
  const querySrc = queryPreview
    || (queryImageUrl ? `/api/proxy-image?url=${encodeURIComponent(queryImageUrl)}` : '')

  const { article, image, confidence, score } = hit
  const evidence = hit.evidence || null
  const inliers = evidence?.orb_inliers ?? 0
  const matches = evidence?.orb_matches || 1
  const hashSim = 1 - ((evidence?.hash_distance) || 0) / 64

  let domain = ''
  try { domain = new URL(article.url).hostname.replace(/^www\./, '') } catch { /* 忽略 */ }
  const providerName = hit.provider ? `引擎 · ${hit.provider}` : ''

  const color = confidence === 'high' ? 'var(--jade)'
    : confidence === 'medium' ? 'var(--gold)' : '#6E7683'

  return (
    <article
      className="hit"
      style={{ animationDelay: `${Math.min(rank, 8) * 55}ms` }}
    >
      <div className="hit-rank">{String(rank).padStart(2, '0')}</div>

      <div className="hit-compare">
        <figure className="frame">
          <img src={querySrc} alt="查询图" />
          <figcaption>查询图</figcaption>
        </figure>
        <div className="hit-arrow">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12h14m0 0-5-5m5 5-5 5" />
          </svg>
        </div>
        <figure className="frame">
          {failed ? (
            <div style={{
              width: 104, height: 78, display: 'grid', placeItems: 'center',
              fontSize: 10, color: 'var(--dim)', background: 'var(--ink-3)',
              border: '1px solid var(--line-2)', borderRadius: 7,
            }}>图源失效</div>
          ) : (
            <img
              src={image.preview || `/api/proxy-image?url=${encodeURIComponent(image.url)}`}
              alt={image.alt || '命中图片'}
              onError={() => setFailed(true)}
            />
          )}
          <figcaption>
            {image.is_cover ? '文章封面'
              : image.position != null ? `正文第 ${image.position + 1} 张` : '网页配图'}
          </figcaption>
        </figure>
      </div>

      <div className="hit-body">
        <h3 className="hit-title" title={article.title}>{article.title}</h3>
        <div className="hit-meta">
          {article.account
            ? <span className="acct">公众号 · {article.account}</span>
            : domain && <span className="acct">{domain}</span>}
          {article.publish_time && <span>{article.publish_time}</span>}
          {image.width ? <span>{image.width}×{image.height}</span> : null}
          {providerName && <span>{providerName}</span>}
        </div>

        {evidence ? (
          <div className="hit-evidence">
            <Bar label="哈希一致" value={hashSim} text={`${evidence.hash_distance}/64`} />
            <Bar
              label="色彩分布"
              value={evidence.hist_similarity}
              text={`${(evidence.hist_similarity * 100).toFixed(0)}%`}
            />
            <Bar
              label="几何内点"
              value={inliers / matches}
              text={`${inliers}/${evidence.orb_matches}`}
            />
          </div>
        ) : hit.snippet ? (
          <div className="hit-snippet">{hit.snippet}</div>
        ) : null}

        <div className="hit-actions">
          <a className="btn-link" href={article.url} target="_blank" rel="noreferrer">
            打开原文
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 17 17 7M17 7H9m8 0v8" />
            </svg>
          </a>
          <a className="btn-link plain" href={image.url} target="_blank" rel="noreferrer">
            查看原图
          </a>
        </div>
      </div>

      <div className="hit-score">
        <Ring score={score} color={color} />
        <span className={`badge ${confidence}`}>{CONF_TEXT[confidence] || confidence}</span>
      </div>
    </article>
  )
}
