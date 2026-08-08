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

  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`))
  page.on('response', (response) => {
    if (response.url().includes('/api/search-v2') && response.status() >= 400) {
      requestFailures.push(`${response.status()} ${response.url()}`)
    }
  })

  await page.goto(`${BASE_URL}/games/pokemon`, { waitUntil: 'networkidle', timeout: 60000 })
  await page.locator('h1').filter({ hasText: 'Pokémon' }).waitFor({ timeout: 15000 })
  await page.getByText('21.065 Cards', { exact: true }).waitFor()
  await page.getByText('27.241 Variants', { exact: true }).waitFor()
  await page.getByText('23 filtros específicos de Pokémon', { exact: true }).waitFor()

  const searchInput = page.locator('.search-input').first()
  assert.match(await searchInput.getAttribute('placeholder'), /Pikachu/)
  await searchInput.fill('Pikachu')
  await searchInput.press('Enter')
  await page.locator('.sv2-results-grid').waitFor({ timeout: 20000 })
  const normalCards = page.locator('.sv2-result-card:not(.sv2-result-card-print)')
  assert.ok(await normalCards.count() > 0, 'normal Pokémon search rendered no card results')
  assert.match((await normalCards.first().innerText()).toLowerCase(), /pikachu/)
  await page.screenshot({ path: path.join(OUTPUT_DIR, 'pokemon-desktop-search.png'), fullPage: false })

  await page.locator('.sv2-advanced-toggle').click()
  await page.locator('.sv2-advanced.is-open').waitFor()
  await page.locator('.sv2-quick-types').waitFor()
  await page.locator('.sv2-quick-stage').waitFor()
  await page.locator('.sv2-quick-rarity').waitFor()
  await page.locator('.sv2-quick-regulation_mark').waitFor()
  await page.locator('.sv2-quick-finish').waitFor()
  await page.locator('.sv2-quick-stamp').waitFor()

  const finishInput = page.locator('.sv2-quick-finish input')
  await finishInput.fill('holo')
  const holoOption = page.locator('.sv2-quick-finish .sv2-picker-option').filter({ hasText: /holo/i }).first()
  await holoOption.waitFor({ timeout: 15000 })
  await holoOption.click()
  await page.locator('.sv2-quick-apply').click()
  await page.locator('.sv2-result-card-print').first().waitFor({ timeout: 20000 })
  const exactPrintText = (await page.locator('.sv2-result-card-print').first().innerText()).toLowerCase()
  assert.match(exactPrintText, /holo/, 'advanced Holo result does not surface Holo identity')
  assert.match(await page.locator('.sv2-results-head').innerText(), /Exact prints/)
  await page.screenshot({ path: path.join(OUTPUT_DIR, 'pokemon-desktop-advanced-holo.png'), fullPage: false })

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
  await page.screenshot({ path: path.join(OUTPUT_DIR, 'pokemon-mobile.png'), fullPage: false })

  await browser.close()

  if (requestFailures.length) failures.push(...requestFailures.map((row) => `search-v2-response: ${row}`))
  if (failures.length) {
    throw new Error(`Visual QA failures:\n${failures.join('\n')}`)
  }

  const report = {
    status: 'pass',
    game: 'pokemon',
    checked: [
      'desktop hero and certified counters',
      'normal Pikachu search',
      'six Pokémon quick-filter controls',
      'Holo advanced exact-print result',
      'Pokémon physical badge visibility',
      'mobile viewport without horizontal overflow',
      'Search V2 BFF responses without HTTP errors',
      'no browser page errors',
    ],
    screenshots: fs.readdirSync(OUTPUT_DIR).sort(),
  }
  fs.writeFileSync(path.join(OUTPUT_DIR, 'report.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify(report, null, 2))
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
