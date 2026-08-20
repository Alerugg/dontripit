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
  variant = 'default',
  type = 'search',
}) {
  const listId = useId()
  const wrapperRef = useRef(null)
  const inputRowRef = useRef(null)
  const [isOpen, setIsOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [dropdownWidth, setDropdownWidth] = useState(0)
  const hasSuggestions = suggestions.length > 0
  const cleanQuery = (value || '').trim()
  const hasMeaningfulQuery = (value || '').trim().length >= 1
  const activeDescendant = isOpen && activeIndex >= 0 && suggestions[activeIndex]
    ? `${listId}-${activeIndex}`
    : undefined

  useEffect(() => {
    if (!value?.trim()) {
      setIsOpen(false)
      setActiveIndex(-1)
    }
  }, [value])

  useEffect(() => {
    if (!suggestions.length && activeIndex >= 0) {
      setActiveIndex(-1)
      return
    }
    if (activeIndex >= suggestions.length) setActiveIndex(suggestions.length - 1)
  }, [activeIndex, suggestions])

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
    function handleDocumentKeyDown(event) {
      if (event.key === 'Escape') {
        setIsOpen(false)
        setActiveIndex(-1)
      }
    }
    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleDocumentKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleDocumentKeyDown)
    }
  }, [])

  function runFullSearch() {
    if (!hasMeaningfulQuery) return
    onSubmit?.()
    setIsOpen(false)
    setActiveIndex(-1)
  }

  function handleKeyDown(event) {
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.length === 0) return

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
      if (isOpen && activeIndex >= 0 && suggestions[activeIndex]) {
        onSuggestionSelect?.(suggestions[activeIndex])
        setIsOpen(false)
        return
      }
      runFullSearch()
    }
  }

  function handleChange(event) {
    const nextValue = event.target.value
    onChange(nextValue)
    setActiveIndex(-1)
    setIsOpen(Boolean(nextValue.trim()))
  }

  return (
    <div className={`search-input-shell search-input-shell-${variant} ${isOpen ? 'search-input-shell-open' : ''}`} ref={wrapperRef}>
      <div className={`search-input-row search-input-row-${variant}`} ref={inputRowRef}>
        <input
          type={type}
          role="combobox"
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onFocus={() => value?.trim() && setIsOpen(true)}
          placeholder={placeholder}
          className={`input search-input search-input-${variant}`}
          aria-label="Buscar en el catálogo"
          aria-expanded={isOpen}
          aria-controls={listId}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-activedescendant={activeDescendant}
          autoComplete="off"
        />
        <button type="button" className={`primary-btn search-submit search-submit-${variant}`} onClick={runFullSearch} aria-label="Buscar todos los resultados">
          Buscar
        </button>
      </div>

      {isOpen ? (
        <div
          className={`suggestions-popover panel-soft suggestions-popover-${variant}`}
          role="presentation"
          style={dropdownWidth ? { width: `${dropdownWidth}px` } : undefined}
        >
          <div className="suggestions-header">
            <div className="suggestions-heading">
              <strong>Sugerencias</strong>
              <small>Enter busca todo; las flechas abren una coincidencia exacta</small>
            </div>
            <button type="button" className="suggestions-close" onClick={() => setIsOpen(false)} aria-label="Cerrar sugerencias">×</button>
          </div>

          {hasMeaningfulQuery ? (
            <button type="button" className="v5-view-all" onClick={runFullSearch}>
              <span className="v5-view-all-icon" aria-hidden="true">⌕</span>
              <span className="v5-view-all-copy">
                <strong>Ver todos los resultados para “{cleanQuery}”</strong>
                <small>Cartas canónicas primero · puedes cambiar a prints o sets después</small>
              </span>
            </button>
          ) : null}

          {!hasMeaningfulQuery ? <p className="suggestions-empty">Empieza a escribir para buscar.</p> : null}
          {hasMeaningfulQuery && suggestionsLoading ? <p className="suggestions-empty">Buscando coincidencias…</p> : null}
          {hasMeaningfulQuery && !suggestionsLoading && !hasSuggestions ? <p className="suggestions-empty">Sin sugerencias directas. Pulsa Enter para buscar igualmente.</p> : null}

          {hasMeaningfulQuery && !suggestionsLoading && hasSuggestions ? (
            <ul id={listId} className="suggestions-list" role="listbox">
              {suggestions.map((item, index) => (
                <SuggestionRow
                  key={`${item.type || 'card'}-${item.id || item.card_id || item.name}-${index}`}
                  item={item}
                  id={`${listId}-${index}`}
                  active={index === activeIndex}
                  onMouseEnter={() => setActiveIndex(index)}
                  onSelect={(nextItem) => {
                    onSuggestionSelect?.(nextItem)
                    setIsOpen(false)
                  }}
                />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
