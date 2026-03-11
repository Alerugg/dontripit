'use client'

export default function SearchBar({ value, onChange, placeholder = 'Busca cartas, collector number o set code...' }) {
  return (
    <div className="search-shell">
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="search-input"
      />
    </div>
  )
}
