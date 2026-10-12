const json = async (res) => {
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`
    try {
      const data = await res.json()
      msg = data.detail || data.error || msg
    } catch { /* 忽略非 JSON 响应 */ }
    throw new Error(msg)
  }
  return res.json()
}

export async function searchImage({ file, imageUrl, topK = 10 }) {
  const form = new FormData()
  if (file) form.append('file', file)
  if (imageUrl) form.append('image_url', imageUrl)
  form.append('top_k', String(topK))
  const res = await fetch('/api/search', { method: 'POST', body: form })
  return json(res)
}

export async function importArticles(urls) {
  const res = await fetch('/api/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls }),
  })
  return json(res)
}

export async function importStatus(jobId) {
  const res = await fetch(`/api/import/${jobId}`)
  return json(res)
}

export const getStats = () => fetch('/api/stats').then(json)
export const getArticles = (q = '') => fetch(`/api/articles?q=${encodeURIComponent(q)}`).then(json)
export const deleteArticle = (id) => fetch(`/api/articles/${id}`, { method: 'DELETE' }).then(json)
