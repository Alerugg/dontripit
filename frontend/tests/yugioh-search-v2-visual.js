const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const BASE_URL = process.env.QA_BASE_URL || 'http://127.0.0.1:3000'
const OUTPUT_DIR = process.env.QA_SCREENSHOT_DIR || '/tmp/yugioh-visual-qa'

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 })
  const page = await context.newPage()
  const failures = []
  const requestFailures = []
  const resourceLoadErrors = []
  let stage = 'boot'

  async function snapshot(name) {
    await page.screenshot({ path: path.join(OUTPUT_DIR, `${name}.png`), fullPage: false })
  }

  async function diagnostic() {
    return {
      stage,
      url: page.url(),
      advancedOpenCount: await page.locator('.sv2-advanced.is-open').count().catch(() => -1),
      quickSet: await page.locator('.sv2-quick-set').count().catch(() => -1),
      quickRelease: await page.locator('.sv2-quick-release').count().catch(() => -1),
      quickCardClass: await page.locator('.sv2-quick-card_class').count().catch(() => -1),
      quickAttribute: await page.locator('.sv2-quick-attribute').count().catch(() => -1),
      quickArchetype: await page.locator('.sv2-quick-archetype').count().catch(() => -1),
      quickRarity: await page.locator('.sv2-quick-rarity').count().catch(() => -1),
      normalCards: await page.locator('.sv2-result-card:not(.sv2-result-card-print)').count().catch(() => -1),
      exactPrints: await page.locator('.sv2-result-card-print').count().catch(() => -1),
      requestFailures,
      resourceLoadErrors,
      browserFailures: failures,
    }
  }

  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    if (/Failed to load resource:.*(?:401|404|504|Unauthorized|Not Found|Gateway Timeout)/i.test(text)
      || /net::ERR_SSL_PROTOCOL_ERROR/i.test(text)) {
      resourceLoadErrors.push(text)
      return
    }
    failures.push(`console: ${text}`)
  })
  page.on('response', (response) => {
    if (response.url().includes('/api/search-v2') && response.status() >= 400) {
      requestFailures.push(`${response.status()} ${response.url()}`)
    }
  })

  try {
    stage = 'desktop_home'
    await page.goto(`${BASE_URL}/games/yugioh`, { waitUntil: 'networkidle', timeout: 60000 })
    await page.getByAltText('Yu-Gi-Oh!').waitFor({ timeout: 15000 })
    await page.getByRole('heading', { name: 'Busca una carta sin perderte en el catálogo.' }).waitFor()
    const sectionNav = page.getByRole('navigation', { name: 'Secciones de Yu-Gi-Oh!' })
    for (const label of ['Buscar', 'Sellado', 'Lanzamientos', 'Sets', 'Noticias']) {
      await sectionNav.getByRole('link', { name: label, exact: true }).waitFor()
    }
    await snapshot('yugioh-desktop-home')

    stage = 'advanced_open'
    const advancedButton = page.getByRole('button', { name: /Afinar búsqueda/i }).first()
    await advancedButton.click()
    await page.locator('.sv2-advanced.is-open').waitFor({ timeout: 10000 })
    for (const selector of [
      '.sv2-quick-set',
      '.sv2-quick-release',
      '.sv2-quick-card_class',
      '.sv2-quick-attribute',
      '.sv2-quick-archetype',
      '.sv2-quick-rarity',
    ]) {
      await page.locator(selector).waitFor({ timeout: 10000 })
    }
    await snapshot('yugioh-desktop-advanced-open')
    await page.getByRole('button', { name: /Cerrar filtros/i }).click()

    stage = 'natural_search'
    const searchInput = page.locator('.search-input').first()
    assert.match(await searchInput.getAttribute('placeholder'), /Dark Magician/)
    await searchInput.fill('Dark Magician')
    await searchInput.press('Enter')
    await page.locator('.sv2-results-grid').waitFor({ timeout: 20000 })
    const normalCards = page.locator('.sv2-result-card:not(.sv2-result-card-print)')
    assert.ok(await normalCards.count() > 0, 'normal Yu-Gi-Oh search rendered no card results')
    assert.match((await normalCards.first().innerText()).toLowerCase(), /dark magician/)
    assert.match((await normalCards.first().innerText()).toLowerCase(), /dark/)
    await snapshot('yugioh-desktop-search')

    stage = 'monster_dark_quick_filter'
    await page.getByRole('button', { name: /Afinar búsqueda/i }).first().click()
    await page.locator('.sv2-advanced.is-open').waitFor({ timeout: 10000 })
    const monsterChip = page.locator('.sv2-quick-card_class .sv2-chip').filter({ hasText: /^Monster$/i }).first()
    const darkChip = page.locator('.sv2-quick-attribute .sv2-chip').filter({ hasText: /^DARK$/i }).first()
    await monsterChip.waitFor({ timeout: 10000 })
    await darkChip.waitFor({ timeout: 10000 })
    await monsterChip.click()
    await darkChip.click()
    await page.locator('.sv2-quick-apply').click()
    await page.locator('.sv2-result-card-print').first().waitFor({ timeout: 20000 })
    const exactPrintText = (await page.locator('.sv2-result-card-print').first().innerText()).toLowerCase()
    assert.match(exactPrintText, /dark magician/, 'Monster + DARK did not preserve Dark Magician query')
    assert.match(exactPrintText, /monster/, 'exact Yu-Gi-Oh print does not surface Monster evidence')
    assert.match(exactPrintText, /dark/, 'exact Yu-Gi-Oh print does not surface DARK evidence')
    assert.match(await page.locator('.sv2-results-head').innerText(), /Versiones que coinciden/)
    await snapshot('yugioh-desktop-advanced-monster-dark')

    stage = 'mobile'
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${BASE_URL}/games/yugioh`, { waitUntil: 'networkidle', timeout: 60000 })
    await page.getByAltText('Yu-Gi-Oh!').waitFor({ timeout: 15000 })
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }))
    assert.ok(
      dimensions.scrollWidth <= dimensions.innerWidth + 2,
      `mobile horizontal overflow: scrollWidth=${dimensions.scrollWidth}, innerWidth=${dimensions.innerWidth}`,
    )
    assert.ok(await page.locator('.search-input').first().isVisible(), 'mobile search input is not visible')
    await snapshot('yugioh-mobile')

    stage = 'final_checks'
    if (requestFailures.length) failures.push(...requestFailures.map((row) => `search-v2-response: ${row}`))
    if (failures.length) throw new Error(`Browser/network failures:\n${failures.join('\n')}`)

    const report = {
      status: 'pass',
      game: 'yugioh',
      checked: [
        'desktop collector hero and section navigation',
        'full Advanced Search panel opens',
        'six Yu-Gi-Oh quick-filter controls',
        'normal Dark Magician search',
        'Monster + DARK returns matching physical versions',
        'source-backed Yu-Gi-Oh identity badges',
        'mobile viewport without horizontal overflow',
        'Search V2 BFF responses without HTTP errors',
        'no browser page errors or fatal console errors',
      ],
      resourceLoadErrorsObserved: resourceLoadErrors.length,
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
