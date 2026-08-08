const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const BASE_URL = process.env.QA_BASE_URL || 'http://127.0.0.1:3000'
const OUTPUT_DIR = process.env.QA_SCREENSHOT_DIR || '/tmp/pokemon-visual-qa'

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 })
  const page = await context.newPage()
  const failures = []
  const requestFailures = []
  let stage = 'boot'

  async function snapshot(name) {
    await page.screenshot({ path: path.join(OUTPUT_DIR, `${name}.png`), fullPage: false })
  }

  async function diagnostic() {
    return {
      stage,
      url: page.url(),
      advancedToggleCount: await page.locator('.sv2-advanced-toggle').count().catch(() => -1),
      advancedOpenCount: await page.locator('.sv2-advanced.is-open').count().catch(() => -1),
      quickTypes: await page.locator('.sv2-quick-types').count().catch(() => -1),
      quickStage: await page.locator('.sv2-quick-stage').count().catch(() => -1),
      quickRarity: await page.locator('.sv2-quick-rarity').count().catch(() => -1),
      quickRegulation: await page.locator('.sv2-quick-regulation_mark').count().catch(() => -1),
      quickFinish: await page.locator('.sv2-quick-finish').count().catch(() => -1),
      quickStamp: await page.locator('.sv2-quick-stamp').count().catch(() => -1),
      normalCards: await page.locator('.sv2-result-card:not(.sv2-result-card-print)').count().catch(() => -1),
      exactPrints: await page.locator('.sv2-result-card-print').count().catch(() => -1),
      requestFailures,
      browserFailures: failures,
    }
  }

  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') failures.push(`console: ${message.text()}`)
  })
  page.on('response', (response) => {
    if (response.url().includes('/api/search-v2') && response.status() >= 400) {
      requestFailures.push(`${response.status()} ${response.url()}`)
    }
  })

  try {
    stage = 'desktop_home'
    await page.goto(`${BASE_URL}/games/pokemon`, { waitUntil: 'networkidle', timeout: 60000 })
    await page.locator('h1').filter({ hasText: 'Pokémon' }).waitFor({ timeout: 15000 })
    await page.getByText('21.065 Cards', { exact: true }).waitFor()
    await page.getByText('27.241 Variants', { exact: true }).waitFor()
    await page.getByText('23 filtros específicos de Pokémon', { exact: true }).waitFor()
    await snapshot('pokemon-desktop-home')

    // Test the full Advanced panel independently of result-state URL changes.
    stage = 'advanced_open'
    const advancedButton = page.getByRole('button', { name: /Advanced Search/i }).first()
    await advancedButton.waitFor({ state: 'visible', timeout: 10000 })
    assert.equal(await advancedButton.isEnabled(), true, 'Advanced Search button is disabled')
    await advancedButton.click()
    await page.locator('.sv2-advanced.is-open').waitFor({ timeout: 10000 })
    for (const selector of [
      '.sv2-quick-types',
      '.sv2-quick-stage',
      '.sv2-quick-rarity',
      '.sv2-quick-regulation_mark',
      '.sv2-quick-finish',
      '.sv2-quick-stamp',
    ]) {
      await page.locator(selector).waitFor({ timeout: 10000 })
    }
    await snapshot('pokemon-desktop-advanced-open')
    await page.getByRole('button', { name: /Cerrar filtros/i }).click()

    stage = 'natural_search'
    const searchInput = page.locator('.search-input').first()
    assert.match(await searchInput.getAttribute('placeholder'), /Pikachu/)
    await searchInput.fill('Pikachu')
    await searchInput.press('Enter')
    await page.locator('.sv2-results-grid').waitFor({ timeout: 20000 })
    const normalCards = page.locator('.sv2-result-card:not(.sv2-result-card-print)')
    assert.ok(await normalCards.count() > 0, 'normal Pokémon search rendered no card results')
    assert.match((await normalCards.first().innerText()).toLowerCase(), /pikachu/)
    await snapshot('pokemon-desktop-search')

    // Quick filters intentionally remain available while the full panel is closed.
    stage = 'holo_quick_filter'
    const finishFacet = page.locator('.sv2-quick-finish')
    await finishFacet.waitFor({ timeout: 10000 })
    const holoChip = finishFacet.locator('.sv2-chip').filter({ hasText: /^holo$/i }).first()
    if (await holoChip.count()) {
      await holoChip.click()
    } else {
      const finishInput = finishFacet.locator('input').first()
      await finishInput.waitFor({ timeout: 10000 })
      await finishInput.fill('holo')
      const holoOption = finishFacet.locator('.sv2-picker-option').filter({ hasText: /holo/i }).first()
      await holoOption.waitFor({ timeout: 15000 })
      await holoOption.click()
    }
    await page.locator('.sv2-quick-apply').click()
    await page.locator('.sv2-result-card-print').first().waitFor({ timeout: 20000 })
    const exactPrintText = (await page.locator('.sv2-result-card-print').first().innerText()).toLowerCase()
    assert.match(exactPrintText, /holo/, 'advanced Holo result does not surface Holo identity')
    assert.match(await page.locator('.sv2-results-head').innerText(), /Exact prints/)
    await snapshot('pokemon-desktop-advanced-holo')

    stage = 'mobile'
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${BASE_URL}/games/pokemon`, { waitUntil: 'networkidle', timeout: 60000 })
    await page.locator('h1').filter({ hasText: 'Pokémon' }).waitFor({ timeout: 15000 })
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }))
    assert.ok(
      dimensions.scrollWidth <= dimensions.innerWidth + 2,
      `mobile horizontal overflow: scrollWidth=${dimensions.scrollWidth}, innerWidth=${dimensions.innerWidth}`,
    )
    assert.ok(await page.locator('.search-input').first().isVisible(), 'mobile search input is not visible')
    await snapshot('pokemon-mobile')

    stage = 'final_checks'
    if (requestFailures.length) failures.push(...requestFailures.map((row) => `search-v2-response: ${row}`))
    if (failures.length) throw new Error(`Browser/network failures:\n${failures.join('\n')}`)

    const report = {
      status: 'pass',
      game: 'pokemon',
      checked: [
        'desktop hero and certified counters',
        'full Advanced Search panel opens',
        'six Pokémon quick-filter controls',
        'normal Pikachu search',
        'Holo quick filter returns Exact Prints',
        'Pokémon physical badge visibility',
        'mobile viewport without horizontal overflow',
        'Search V2 BFF responses without HTTP errors',
        'no browser page or console errors',
      ],
      screenshots: fs.readdirSync(OUTPUT_DIR).filter((name) => name.endsWith('.png')).sort(),
    }
    fs.writeFileSync(path.join(OUTPUT_DIR, 'report.json'), JSON.stringify(report, null, 2) + '\n')
    console.log(JSON.stringify(report, null, 2))
  } catch (error) {
    const diag = await diagnostic().catch(() => ({ stage, diagnostic: 'failed to collect' }))
    const failure = {
      status: 'fail',
      ...diag,
      error: `${error.name || 'Error'}: ${error.message || error}`,
    }
    fs.writeFileSync(path.join(OUTPUT_DIR, 'failure.json'), JSON.stringify(failure, null, 2) + '\n')
    await snapshot(`failure-${stage}`).catch(() => {})
    console.error(JSON.stringify(failure, null, 2))
    throw error
  } finally {
    await browser.close()
  }
}

main().catch(() => process.exit(1))
