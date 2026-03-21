'use client'

import { useId, useMemo, useState } from 'react'
import SuggestionRow from '../catalog/SuggestionRow'

export default function SearchInput({
  value,
  onChange,
  onSubmit,
  suggestions,
  suggestionsLoading,
  onSuggestionSelect,
  placeholder,
}) {
  const [activeIndex, setActiveIndex] = useState(-1)
  const listId = useId()
  const hintId = useId()
  const hasSuggestions = suggestionsLoading || suggestions.length > 0
  const activeDescendant = activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined

  const listLabel = useMemo(() => {
    if (suggestionsLoading) return 'Cargando sugerencias'
    if (suggestions.length === 0) return 'Sin sugerencias'
    return `${suggestions.length} sugerencias disponibles`
  }, [suggestionsLoading, suggestions.length])

  const commitSearch = () => {
    setActiveIndex(-1)
    onSubmit()
  }

  const handleKeyDown = (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!suggestions.length) return
      setActiveIndex((current) => (current + 1) % suggestions.length)
      return
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (!suggestions.length) return
      setActiveIndex((current) => (current <= 0 ? suggestions.length - 1 : current - 1))
      return
    }

    if (event.key === 'Escape') {
      setActiveIndex(-1)
      return
    }

    if (event.key === 'Enter' && activeIndex >= 0 && suggestions[activeIndex]) {
      event.preventDefault()
      onSuggestionSelect(suggestions[activeIndex])
      setActiveIndex(-1)
    }
  }

  return (
    <form
      className="search-combobox"
      onSubmit={(event) => {
        event.preventDefault()
        commitSearch()
      }}
    >
      <div className="search-combobox-inputs">
        <div className="search-field-wrap">
          <input
            className="input search-input"
            value={value}
            onChange={(event) => {
              setActiveIndex(-1)
              onChange(event.target.value)
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            role="combobox"
            aria-expanded={hasSuggestions}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={activeDescendant}
            aria-describedby={hintId}
          />
          <span id={hintId} className="sr-only">Usa flechas para navegar sugerencias y Enter para buscar o seleccionar.</span>

          {hasSuggestions && (
            <div className="suggestions-popover panel" aria-live="polite">
              <p className="sr-only">{listLabel}</p>
              <ul className="suggestions-list" role="listbox" id={listId}>
                {suggestionsLoading && <li className="suggestion-hint">Buscando sugerencias...</li>}
                {!suggestionsLoading && suggestions.map((item, index) => (
                  <SuggestionRow
                    key={`${item.type || 'item'}-${item.id}`}
                    item={item}
                    id={`${listId}-${index}`}
                    active={index === activeIndex}
                    onMouseEnter={() => setActiveIndex(index)}
                    onSelect={(selectedItem) => {
                      setActiveIndex(-1)
                      onSuggestionSelect(selectedItem)
                    }}
                  />
                ))}
              </ul>
            </div>
          )}
        </div>

        <button type="submit" className="primary-btn search-submit">Buscar</button>
      </div>
    </form>
  )
}
