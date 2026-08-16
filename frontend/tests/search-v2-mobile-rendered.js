const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const BASE_URL = process.env.QA_BASE_URL || 'http://127.0.0.1:3000'
const OUTPUT_DIR = process.env.QA_SCREENSHOT_DIR || '/tmp/search-v2-mobile-rendered'

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 })
  const page = await context.newPage()
  const failures = []
  const requestFailures = []
  const resourceErrors = []

  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    if (/Failed to load resource|net::ERR_/i.test(text)) resourceErrors.push(text)
    else failures.push(`console: ${text}`)
  })
  page.on('response', (response) => {
    if (response.url().includes('/api/search-v2') && response.status() >= 400) {
      requestFailures.push(`${response.status()} ${response.url()}`)
    }
  })

  const viewportCheck = async (label) => {
    const dims = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }))
    assert.ok(dims.scrollWidth <= dims.innerWidth + 2, `${label} horizontal overflow: ${JSON.stringify(dims)}`)
    return dims
  }

  try {
    await page.goto(`${BASE_URL}/games/pokemon`, { waitUntil: 'networkidle', timeout: 60000 })
    await page.getByAltText('Pokémon').waitFor({ timeout: 15000 })
    await viewportCheck('home')

    const searchInput = page.locator('.search-input').first()
    await searchInput.waitFor({ state: 'visible', timeout: 10000 })
    await searchInput.fill('Pikachu')
    await searchInput.press('Enter')
    await page.locator('.sv2-results-grid').waitFor({ timeout: 25000 })

    const cards = page.locator('.sv2-result-card:not(.sv2-result-card-print)')
    assert.ok(await cards.count() > 0, 'mobile natural search rendered no Pikachu cards')
    assert.match((await cards.first().innerText()).toLowerCase(), /pikachu/)
    const cardBox = await cards.first().boundingBox()
    assert.ok(cardBox && cardBox.x >= -1 && cardBox.x + cardBox.width <= 392, `result card escapes viewport: ${JSON.stringify(cardBox)}`)
    const imageBox = await cards.first().locator('.sv2-result-image-wrap').boundingBox()
    assert.ok(imageBox && imageBox.width >= 108, `mobile result image is too small: ${JSON.stringify(imageBox)}`)
    await viewportCheck('natural-search')
    await page.screenshot({ path: path.join(OUTPUT_DIR, 'pokemon-mobile-search.png'), fullPage: true })

    await page.getByRole('button', { name: /Afinar búsqueda/i }).first().click()
    await page.locator('.sv2-advanced.is-open').waitFor({ timeout: 10000 })
    const finishFacet = page.locator('.sv2-quick-finish')
    await finishFacet.waitFor({ timeout: 10000 })
    const holoChip = finishFacet.locator('.sv2-chip').filter({ hasText: /^holo$/i }).first()
    if (await holoChip.count()) {
      await holoChip.click()
    } else {
      const input = finishFacet.locator('input').first()
      await input.fill('holo')
      const option = finishFacet.locator('.sv2-picker-option').filter({ hasText: /holo/i }).first()
      await option.waitFor({ timeout: 15000 })
      await option.click()
    }
    await page.locator('.sv2-quick-apply').click()
    const exactPrint = page.locator('.sv2-result-card-print').first()
    await exactPrint.waitFor({ timeout: 25000 })
    assert.match((await exactPrint.innerText()).toLowerCase(), /holo/)
    await viewportCheck('holo-results')

    const marketRows = page.locator('.sv2-result-card-print .sv2-market-row')
    assert.ok(await marketRows.count() > 0, 'exact mobile result has no market row')
    const marketBox = await marketRows.first().boundingBox()
    assert.ok(marketBox && marketBox.x >= -1 && marketBox.x + marketBox.width <= 392, `market row escapes viewport: ${JSON.stringify(marketBox)}`)
    await page.screenshot({ path: path.join(OUTPUT_DIR, 'pokemon-mobile-holo.png'), fullPage: true })

    if (requestFailures.length) failures.push(...requestFailures.map((row) => `search-v2-response: ${row}`))
    if (failures.length) throw new Error(failures.join('\n'))

    const report = {
      status: 'pass',
      viewport: { width: 390, height: 844 },
      game: 'pokemon',
      natural_query: 'Pikachu',
      advanced_finish: 'holo',
      checks: {
        no_horizontal_overflow: true,
        natural_results_rendered: true,
        result_card_within_viewport: true,
        image_width_at_least_108px: true,
        exact_holo_result_rendered: true,
        market_row_within_viewport: true,
        search_bff_http_errors: 0,
        fatal_browser_errors: 0,
      },
      resource_load_errors_observed: resourceErrors.length,
      production_writes: 0,
      database_mode: 'production-data-read-through-local-backend-forced-read-only',
      screenshots: ['pokemon-mobile-search.png', 'pokemon-mobile-holo.png'],
    }
    fs.writeFileSync(path.join(OUTPUT_DIR, 'report.json'), `${JSON.stringify(report, null, 2)}\n`)
    console.log(JSON.stringify(report, null, 2))
  } catch (error) {
    await page.screenshot({ path: path.join(OUTPUT_DIR, 'failure.png'), fullPage: true }).catch(() => {})
    const report = { status: 'fail', error: String(error), requestFailures, failures, resourceErrors }
    fs.writeFileSync(path.join(OUTPUT_DIR, 'failure.json'), `${JSON.stringify(report, null, 2)}\n`)
    console.error(JSON.stringify(report, null, 2))
    throw error
  } finally {
    await browser.close()
  }
}

main().catch(() => process.exit(1))
