import { useEffect, useRef, useState } from 'react'
import { deleteArticle, getArticles, importArticles, importStatus } from '../api.js'

export default function LibraryPanel({ onStatsChange }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState(null)
  const [err, setErr] = useState('')
  const [articles, setArticles] = useState([])
  const [filter, setFilter] = useState('')
  const timer = useRef(null)

  const loadArticles = (q = '') => {
    getArticles(q).then((d) => setArticles(d.items || [])).catch(() => {})
  }

  useEffect(() => { loadArticles() }, [])
  useEffect(() => () => clearInterval(timer.current), [])

  const startImport = async () => {
    const urls = text.split(/[\n,\s]+/).map((s) => s.trim())
      .filter((s) => s.startsWith('http'))
    if (!urls.length) {
      setErr('请粘贴 mp.weixin.qq.com 开头的文章链接，每行一个')
      return
    }
    setErr('')
    setBusy(true)
    setJob(null)
    try {
      const { job_id, total } = await importArticles(urls)
      setJob({ id: job_id, total, done: 0, status: 'running', results: [] })
      clearInterval(timer.current)
      timer.current = setInterval(async () => {
        try {
          const s = await importStatus(job_id)
          setJob(s)
          if (s.status === 'done') {
            clearInterval(timer.current)
            setBusy(false)
            setText('')
            loadArticles(filter)
            onStatsChange()
          }
        } catch {
          clearInterval(timer.current)
          setBusy(false)
        }
      }, 1200)
    } catch (e) {
      setErr(e.message || '导入失败')
      setBusy(false)
    }
  }

  const onDelete = async (id) => {
    await deleteArticle(id).catch(() => {})
    loadArticles(filter)
    onStatsChange()
  }

  const pct = job && job.total ? Math.round((job.done / job.total) * 100) : 0

  return (
    <>
      <div className="card">
        <div className="card-title"><span className="dot" />扩充索引库</div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={'粘贴公众号文章链接，每行一条：\nhttps://mp.weixin.qq.com/s/xxxxx\nhttps://mp.weixin.qq.com/s/yyyyy'}
        />
        <button
          className="primary"
          style={{ width: '100%', marginTop: 10 }}
          onClick={startImport}
          disabled={busy}
        >
          {busy ? <span className="spinner" /> : '抓取并建索引'}
        </button>

        {err && <div className="alert" style={{ marginTop: 10 }}>{err}</div>}

        {job && (
          <>
            <div className="progress"><i style={{ width: `${pct}%` }} /></div>
            <div className="hint">
              {job.status === 'done'
                ? `完成：${job.done}/${job.total} 篇`
                : `正在抓取 ${job.done}/${job.total} 篇…`}
            </div>
            <div className="job-log">
              {(job.results || []).map((r, i) => (
                <div key={i} className={
                  r.status === 'ok' ? 'ok' : r.status === 'skipped' ? 'skip' : 'bad'
                }>
                  {r.status === 'ok' && `已收录《${r.title}》· ${r.images} 张图`}
                  {r.status === 'skipped' && `已存在，跳过《${r.title}》`}
                  {r.status === 'failed' && `失败：${r.error}`}
                </div>
              ))}
            </div>
          </>
        )}

        <div className="hint">
          系统会抓取正文里的全部微信 CDN 图片，计算感知哈希与局部特征后入库。
          库里的素材越全，溯源命中率越高。
        </div>
      </div>

      <div className="card">
        <div className="card-title"><span className="dot" />已收录文章</div>
        <input
          type="text"
          value={filter}
          placeholder="按标题或公众号名筛选"
          onChange={(e) => {
            setFilter(e.target.value)
            loadArticles(e.target.value)
          }}
        />
        <div className="article-list">
          {articles.length === 0 ? (
            <div className="hint" style={{ marginTop: 4 }}>
              还没有收录任何文章。先在上方导入几篇试试。
            </div>
          ) : articles.map((a) => (
            <div className="article-item" key={a.id}>
              <div className="t">{a.title}</div>
              <div className="m">
                {a.account && <span>{a.account}</span>}
                <span>{a.images} 图</span>
                <a href={a.url} target="_blank" rel="noreferrer">原文</a>
                <button className="del" onClick={() => onDelete(a.id)}>移除</button>
              </div>
              {a.status === 'failed' && <div className="m" style={{ color: '#ff9a93' }}>{a.error}</div>}
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
