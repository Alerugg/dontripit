'use client'

import { useEffect, useId, useRef, useState } from 'react'
import SuggestionRow from '../catalog/SuggestionRow'

export default function SearchInput({
  value,
  onChange,
  onSubmit,
  suggestions = [],
  suggestionsLoading = false,
  onSuggestionSelect,
  placeholder,
}) {
  const listId = useId()
  const wrapperRef = useRef(null)
  const inputRowRef = useRef(null)
  const [isOpen, setIsOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [dropdownWidth, setDropdownWidth] = useState(0)
  const hasSuggestions = suggestions.length > 0

  useEffect(() => {
    if (!value?.trim()) {
      setIsOpen(false)
      setActiveIndex(-1)
      return
    }

    setIsOpen(hasSuggestions || suggestionsLoading)
  }, [value, hasSuggestions, suggestionsLoading])

  useEffect(() => {
    function syncDropdownWidth() {
      setDropdownWidth(inputRowRef.current?.offsetWidth || 0)
    }

    syncDropdownWidth()
    window.addEventListener('resize', syncDropdownWidth)
    return () => window.removeEventListener('resize', syncDropdownWidth)
  }, [])

  useEffect(() => {
    function handlePointerDown(event) {
      if (!wrapperRef.current?.contains(event.target)) {
        setIsOpen(false)
        setActiveIndex(-1)
      }
    }

    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        setIsOpen(false)
        setActiveIndex(-1)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  function handleKeyDown(event) {
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.length === 0) {
      return
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!isOpen) setIsOpen(true)
      setActiveIndex((current) => (current + 1) % suggestions.length)
      return
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (!isOpen) setIsOpen(true)
      setActiveIndex((current) => (current <= 0 ? suggestions.length - 1 : current - 1))
      return
    }

    if (event.key === 'Enter') {
      event.preventDefault()
      if (isOpen && suggestions[activeIndex]) {
        onSuggestionSelect(suggestions[activeIndex])
        setIsOpen(false)
        return
      }

      onSubmit?.()
      setIsOpen(false)
    }
  }

  return (
    <div className={`search-input-shell ${isOpen ? 'search-input-shell-open' : ''}`} ref={wrapperRef}>
      <div className="search-input-row" ref={inputRowRef}>
        <input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => value?.trim() && setIsOpen(true)}
          placeholder={placeholder}
          className="input search-input"
          aria-expanded={isOpen}
          aria-controls={listId}
          aria-autocomplete="list"
        />
        <button type="button" className="primary-btn search-submit" onClick={() => { onSubmit?.(); setIsOpen(false) }}>
          Buscar
        </button>
      </div>

      {isOpen && (
        <div
          className="suggestions-popover panel-soft"
          role="presentation"
          style={dropdownWidth ? { width: `${dropdownWidth}px` } : undefined}
        >
          <div className="suggestions-header">
            <strong>Sugerencias</strong>
            <button type="button" className="suggestions-close" onClick={() => setIsOpen(false)} aria-label="Cerrar sugerencias">
              ×
            </button>
          </div>

          {suggestionsLoading && <p className="suggestions-empty">Buscando coincidencias…</p>}
          {!suggestionsLoading && !hasSuggestions && <p className="suggestions-empty">Sin sugerencias para este término.</p>}

          {!suggestionsLoading && hasSuggestions && (
            <ul id={listId} className="suggestions-list" role="listbox">
              {suggestions.map((item, index) => (
                <SuggestionRow
                  key={`${item.type || 'card'}-${item.id || item.card_id || item.name}-${index}`}
                  item={item}
                  id={`${listId}-${index}`}
                  active={index === activeIndex}
                  onMouseEnter={() => setActiveIndex(index)}
                  onSelect={(nextItem) => {
                    onSuggestionSelect(nextItem)
                    setIsOpen(false)
                  }}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
