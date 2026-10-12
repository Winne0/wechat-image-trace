import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteArticle, getArticles, getProviders, getStats,
  importArticles, importStatus, searchImage,
} from './api.js'
import ResultCard from './components/ResultCard.jsx'
import LibraryPanel from './components/LibraryPanel.jsx'

const Icon = {
  Search: () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
      <circle cx="11" cy="11" r="7" /><path d="m20 20-3.6-3.6" />
    </svg>
  ),
  Upload: () => (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 16V4m0 0L8 8m4-4 4 4" /><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </svg>
  ),
  Arrow: () => (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14m0 0-5-5m5 5-5 5" />
    </svg>
  ),
}

export default function App() {
  const [file, setFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [imageUrl, setImageUrl] = useState('')
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [stats, setStats] = useState({ articles: 0, articles_ok: 0, images: 0 })
  const [scope, setScope] = useState('local')
  const [providers, setProviders] = useState(null)

  const inputRef = useRef(null)
  const previewRef = useRef('')

  const refreshStats = useCallback(() => {
    getStats().then(setStats).catch(() => {})
  }, [])

  useEffect(() => { refreshStats() }, [refreshStats])
  useEffect(() => { getProviders().then(setProviders).catch(() => {}) }, [])

  const pickFile = useCallback((f) => {
    if (!f || !f.type.startsWith('image/')) {
      setError('只支持图片文件（jpg / png / webp / gif / bmp）')
      return
    }
    if (f.size > 16 * 1024 * 1024) {
      setError('图片不能超过 16MB')
      return
    }
    setError('')
    setFile(f)
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    const url = URL.createObjectURL(f)
    previewRef.current = url
    setPreviewUrl(url)
    setImageUrl('')
  }, [])

  // 支持 Ctrl+V 直接粘贴截图
  useEffect(() => {
    const onPaste = (e) => {
      const items = e.clipboardData?.items
      if (!items) return
      for (const it of items) {
        if (it.type.startsWith('image/')) {
          pickFile(it.getAsFile())
          break
        }
      }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [pickFile])

  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
  }, [])

  const runSearch = async (override) => {
    const useFile = override !== undefined ? override : file
    const useUrl = override ? '' : imageUrl.trim()
    if (!useFile && !useUrl) {
      setError('先上传一张图片，或粘贴图片地址')
      return
    }
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const data = await searchImage({
        file: useFile, imageUrl: useUrl || undefined, scope,
      })
      setResult(data)
      refreshStats()
    } catch (e) {
      setError(e.message || '检索失败')
    } finally {
      setLoading(false)
    }
  }

  // 一键体验：用内置的「传播版」示例图搜索
  const useDemoImage = async () => {
    try {
      const res = await fetch('/api/demo-query')
      if (!res.ok) throw new Error('示例图未生成')
      const blob = await res.blob()
      const f = new File([blob], 'demo_query.jpg', { type: 'image/jpeg' })
      if (previewRef.current) URL.revokeObjectURL(previewRef.current)
      const url = URL.createObjectURL(f)
      previewRef.current = url
      setFile(f)
      setPreviewUrl(url)
      setImageUrl('')
      runSearch(f)
    } catch {
      setError('示例图不可用，请先运行 python3 scripts/seed_demo.py')
    }
  }

  const reset = () => {
    setFile(null)
    setPreviewUrl('')
    setImageUrl('')
    setResult(null)
    setError('')
  }

  return (
    <div className="app">
      <header className="top">
        <div className="top-inner">
          <div className="brand">
            <div className="brand-mark"><Icon.Search /></div>
            <div>
              <h1>图溯</h1>
              <p>公众号图片溯源 · 以图反查文章出处</p>
            </div>
          </div>
          <div className="chips">
            <span className="chip">索引图片 <b>{stats.images}</b></span>
            <span className="chip">文章 <b>{stats.articles_ok}</b></span>
          </div>
        </div>
      </header>

      <div className="shell">
        <div className="layout">
          <main>
            <div className="card">
              <div className="card-title"><span className="dot" />上传图片，找它出自哪篇文章</div>

              <div
                className={`dropzone${dragging ? ' dragging' : ''}${previewUrl ? ' has-image' : ''}`}
                onClick={() => inputRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragging(false)
                  pickFile(e.dataTransfer.files?.[0])
                }}
              >
                <input
                  ref={inputRef}
                  type="file"
                  accept="image/*"
                  hidden
                  onChange={(e) => pickFile(e.target.files?.[0])}
                />
                {previewUrl ? (
                  <div className="preview-wrap">
                    <img className="preview-img" src={previewUrl} alt="待检索图片" />
                    <div className="preview-meta">
                      <span className="name">{file?.name || '粘贴的图片'}</span>
                      {file?.size ? <div>大小 {(file.size / 1024).toFixed(0)} KB</div> : null}
                      <div>已就绪，点击「开始溯源」</div>
                    </div>
                  </div>
                ) : (
                  <>
                    <Icon.Upload />
                    <div className="lead" style={{ marginTop: 10 }}>拖拽图片到这里，或点击选择</div>
                    <div className="sub">
                      也可以直接 <kbd>Ctrl</kbd> + <kbd>V</kbd> 粘贴截图<br />
                      支持裁剪、压缩、加水印后的图片二次传播版本
                    </div>
                  </>
                )}
              </div>

              <div className="url-row">
                <input
                  type="text"
                  value={imageUrl}
                  placeholder="或者粘贴图片链接（支持 mmbiz.qpic.cn 等微信图床地址）"
                  onChange={(e) => {
                    setImageUrl(e.target.value)
                    if (e.target.value) {
                      setFile(null)
                      setPreviewUrl('')
                    }
                  }}
                />
                <button className="primary" onClick={() => runSearch()} disabled={loading}>
                  {loading ? <span className="spinner" /> : '开始溯源'}
                </button>
                <button className="ghost" onClick={useDemoImage} disabled={loading}>
                  示例图
                </button>
                {(previewUrl || imageUrl || result) && (
                  <button className="ghost" onClick={reset} disabled={loading}>重置</button>
                )}
              </div>

              <div className="scope">
                <button
                  className={scope === 'local' ? 'on' : ''}
                  onClick={() => setScope('local')}
                >本地指纹库</button>
                <button
                  className={scope === 'web' ? 'on' : ''}
                  onClick={() => setScope('web')}
                >全网检索</button>
              </div>
              <div className="scope-note">
                {scope === 'local'
                  ? '在你收录过的公众号文章图库内做指纹比对 · 毫秒级 · 完全离线'
                  : (providers?.enabled?.length
                    ? `已启用引擎：${providers.enabled.join('、')} · 结果按与公众号的关联度排序`
                    : '尚未配置以图搜图引擎的 API Key，全网检索暂时不可用（见 README「全网检索」一节）')}
              </div>

              {error && <div className="alert" style={{ marginTop: 14 }}>{error}</div>}
            </div>

            {loading && (
              <div className="card" style={{ marginTop: 18 }}>
                <div className="searching">
                  <span className="spinner" />
                  {scope === 'web' ? '正在调用以图搜图引擎检索全网…' : '正在指纹库里比对…'}
                </div>
              </div>
            )}

            {result && !loading && (
              <>
                <div className="results-head">
                  <h2>{result.hits.length ? '匹配结果' : '没有找到匹配'}</h2>
                  <span>
                    {scope === 'web'
                      ? `全网命中 ${result.hits.length} 条 · 公众号来源优先`
                      : `已比对 ${result.total_indexed} 张索引图片 · 按置信度排序`}
                  </span>
                </div>

                {result.hits.length === 0 ? (
                  <div className="card">
                    <div className="empty">
                      {scope === 'web' ? (
                        <>
                          <div className="big">全网没有找到这张图的出处</div>
                          可能原因：图片太小众、被重度裁剪或二次创作，也可能引擎配额已用尽。<br />
                          可换用本地指纹库，或先收录相关文章再搜。
                        </>
                      ) : (
                        <>
                          <div className="big">这张图不在当前索引库里</div>
                          可能原因：文章还没被收录进库，或图片被重度裁剪 / 二次创作。<br />
                          在右侧粘贴原文链接把它加进索引库，就能被搜到了。
                        </>
                      )}
                    </div>
                  </div>
                ) : (
                  result.hits.map((hit, i) => (
                    <ResultCard
                      key={`${hit.article?.id || hit.source_url || 'w'}-${i}`}
                      hit={hit}
                      rank={i + 1}
                      queryPreview={previewUrl}
                      queryImageUrl={imageUrl}
                    />
                  ))
                )}
              </>
            )}
          </main>

          <aside className="side">
            <LibraryPanel
              onStatsChange={refreshStats}
              stats={stats}
            />
          </aside>
        </div>

        <footer className="foot">
          图片素材来源限定微信公众号（mp.weixin.qq.com）· 指纹比对在本地完成
        </footer>
      </div>
    </div>
  )
}
