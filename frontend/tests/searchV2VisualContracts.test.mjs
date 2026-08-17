import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('search suggestions open from user interaction, not URL-hydrated value alone', async () => {
  const source = await fs.readFile(new URL('../components/search/SearchInput.js', import.meta.url), 'utf8')

  assert.match(source, /function handleChange\(event\)/)
  assert.match(source, /setIsOpen\(Boolean\(nextValue\.trim\(\)\)\)/)
  assert.match(source, /onFocus=\{\(\) => value\?\.trim\(\) && setIsOpen\(true\)\}/)
  assert.doesNotMatch(source, /useEffect\(\(\) => \{\s*setIsOpen\(Boolean\(value\?\.trim\(\)\)\)/)
})

test('Search V2 cards render catalog images through FallbackImage proxy normalization', async () => {
  const source = await fs.readFile(new URL('../components/searchV2/SearchV2Results.js', import.meta.url), 'utf8')

  assert.match(source, /import FallbackImage/)
  assert.match(source, /<FallbackImage/)
  assert.doesNotMatch(source, /<img\s+src=/)
})

test('quick filters stay visible and are not duplicated in the full advanced groups', async () => {
  const source = await fs.readFile(new URL('../components/searchV2/AdvancedSearchPanel.js', import.meta.url), 'utf8')

  assert.match(source, /QUICK_FILTER_KEYS/)
  assert.match(source, /Quick filters/)
  assert.match(source, /fullGroups/)
  assert.match(source, /filter\(\(facet\) => !QUICK_FILTER_KEYS\.has\(facet\.key\)\)/)
})
