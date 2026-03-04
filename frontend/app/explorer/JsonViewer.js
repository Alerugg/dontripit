'use client'

export default function JsonViewer({ data }) {
  return (
    <pre className="max-h-80 overflow-auto rounded border border-gray-300 bg-white p-3 text-xs text-gray-900">
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}
