'use client'

export default function JsonViewer({ data }) {
  return (
    <pre className="max-h-80 overflow-auto rounded border border-gray-700 bg-[#0b0b0b] p-3 text-xs text-[#eaeaea]">
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}
