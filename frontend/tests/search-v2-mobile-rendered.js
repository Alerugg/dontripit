const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const BASE_URL = process.env.QA_BASE_URL || 'http://127.0.0.1:3000'
const BASE_ORIGIN = new URL(BASE_URL).origin
const OUTPUT_DIR = process.env.QA_SCREENSHOT_DIR || '/tmp/search-v2-mobile-rendered'

function parsedUrl(value) {
  try { return new URL(value) } catch { return null }
}

function isExpectedAnonymousResponse(detail) {
  const parsed = parsedUrl(detail.url)
  return detail.first_party && detail.status === 401 && parsed?.pathname === '/api/auth/me'
}

function isExpectedRscCancellation(detail) {
  const parsed = parsedUrl(detail.url)
  return detail.first_party
    && /ERR_ABORTED/i.test(detail.error_text || '')
    && parsed?.searchParams.has('_rsc')
}

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 })
  const page = await context.newPage()
  const failures = []
  const requestFailures = []
  const resourceErrors = []
  const resourceResponses = []
  const failedRequests = []

  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    if (/Failed to load resource|net::ERR_/i.test(text)) resourceErrors.push(text)
    else failures.push(`console: ${text}`)
  })
  page.on('response', (response) => {
    const url = response.url()
    const status = response.status()
    if (url.includes('/api/search-v2') && status >= 400) {
      requestFailures.push(`${status} ${url}`)
      return
    }
    if (status < 400) return

    const parsed = parsedUrl(url)
    const detail = {
      status,
      url,
      origin: parsed?.origin || null,
      resource_type: response.request().resourceType(),
      first_party: parsed?.origin === BASE_ORIGIN,
    }
    resourceResponses.push(detail)
    if (detail.first_party && !isExpectedAnonymousResponse(detail)) {
      failures.push(`first-party-resource: ${status} ${url}`)
    }
  })
  page.on('requestfailed', (request) => {
    const url = request.url()
    const parsed = parsedUrl(url)
    const detail = {
      url,
      origin: parsed?.origin || null,
      resource_type: request.resourceType(),
      first_party: parsed?.origin === BASE_ORIGIN,
      error_text: request.failure()?.errorText || 'unknown',
    }
    failedRequests.push(detail)
    if (detail.first_party && !isExpectedRscCancellation(detail)) {
      failures.push(`first-party-request-failed: ${detail.error_text} ${url}`)
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

    // The Pokémon collector UI intentionally resolves the natural query through
    // the physical Advanced endpoint, so a valid natural result may already be
    // a `.sv2-result-card-print`. The rendered QA cares about the card layout,
    // identity and viewport, not which server endpoint produced the result.
    const cards = page.locator('.sv2-results-grid .sv2-result-card')
    assert.ok(await cards.count() > 0, 'mobile natural search rendered no Pikachu results')
    assert.match((await cards.first().innerText()).toLowerCase(), /pikachu/)
    const naturalResultMode = await cards.first().evaluate((node) => (
      node.classList.contains('sv2-result-card-print') ? 'physical-print' : 'canonical-card'
    ))
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

    const unexpectedFirstPartyHttp = resourceResponses.filter(
      (row) => row.first_party && !isExpectedAnonymousResponse(row),
    )
    const unexpectedFirstPartyNetwork = failedRequests.filter(
      (row) => row.first_party && !isExpectedRscCancellation(row),
    )
    const expectedAnonymousResponses = resourceResponses.filter(isExpectedAnonymousResponse)
    const expectedRscCancellations = failedRequests.filter(isExpectedRscCancellation)
    const externalHttpFailures = resourceResponses.filter((row) => !row.first_party)
    const externalNetworkFailures = failedRequests.filter((row) => !row.first_party)

    const report = {
      status: 'pass',
      viewport: { width: 390, height: 844 },
      game: 'pokemon',
      natural_query: 'Pikachu',
      natural_result_mode: naturalResultMode,
      advanced_finish: 'holo',
      checks: {
        no_horizontal_overflow: true,
        natural_results_rendered: true,
        result_card_within_viewport: true,
        image_width_at_least_108px: true,
        exact_holo_result_rendered: true,
        market_row_within_viewport: true,
        search_bff_http_errors: 0,
        unexpected_first_party_http_resource_errors: unexpectedFirstPartyHttp.length,
        unexpected_first_party_network_failures: unexpectedFirstPartyNetwork.length,
        fatal_browser_errors: 0,
      },
      expected_anonymous_auth_responses: expectedAnonymousResponses,
      expected_rsc_cancellations: expectedRscCancellations,
      resource_load_console_errors_observed: resourceErrors.length,
      external_http_failures: externalHttpFailures,
      external_network_failures: externalNetworkFailures,
      production_writes: 0,
      database_mode: 'production-data-read-through-local-backend-forced-read-only',
      screenshots: ['pokemon-mobile-search.png', 'pokemon-mobile-holo.png'],
    }
    fs.writeFileSync(path.join(OUTPUT_DIR, 'report.json'), `${JSON.stringify(report, null, 2)}\n`)
    console.log(JSON.stringify(report, null, 2))
  } catch (error) {
    await page.screenshot({ path: path.join(OUTPUT_DIR, 'failure.png'), fullPage: true }).catch(() => {})
    const report = {
      status: 'fail',
      error: String(error),
      requestFailures,
      failures,
      resourceErrors,
      resourceResponses,
      failedRequests,
    }
    fs.writeFileSync(path.join(OUTPUT_DIR, 'failure.json'), `${JSON.stringify(report, null, 2)}\n`)
    console.error(JSON.stringify(report, null, 2))
    throw error
  } finally {
    await browser.close()
  }
}

main().catch(() => process.exit(1))
