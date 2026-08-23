const fs = require('fs')
const path = require('path')

const root = path.join(__dirname, '..')
const client = fs.readFileSync(path.join(root, 'lib/catalog/client.js'), 'utf8')
const marketCss = fs.readFileSync(path.join(root, 'app/price-first-market.css'), 'utf8')

describe('final frontend polish contract', () => {
  test('interactive catalog requests fail closed instead of spinning forever', () => {
    expect(client).toContain('const SEARCH_TIMEOUT_MS = 15000')
    expect(client).toContain('const SUGGEST_TIMEOUT_MS = 8000')
    expect(client).toContain("payload === null")
    expect(client).toContain("timeoutError.name = 'TimeoutError'")
    expect(client).toContain('timeoutMs: options.timeoutMs ?? SEARCH_TIMEOUT_MS')
  })

  test('mobile Cardmarket window keeps all three metrics balanced', () => {
    expect(marketCss).toContain('.v15-price-window-grid')
    expect(marketCss).toContain('grid-template-columns: repeat(3, minmax(0, 1fr)) !important;')
    expect(marketCss).toContain('.v15-print-market-primary > small')
    expect(marketCss).toContain('font-size: .64rem;')
  })
})
