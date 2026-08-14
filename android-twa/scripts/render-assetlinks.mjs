import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const TEMPLATE = path.resolve(HERE, '..', 'assetlinks.template.json')
const outputPath = path.resolve(process.argv[2] || path.join(HERE, '..', 'assetlinks.generated.json'))

function normalizeFingerprint(value, label) {
  if (!value || !String(value).trim()) return null
  const hex = String(value).replace(/[^0-9a-f]/gi, '').toUpperCase()
  if (hex.length !== 64 || !/^[0-9A-F]{64}$/.test(hex)) {
    throw new Error(`${label} must be a SHA-256 certificate fingerprint (32 bytes / 64 hex characters)`)
  }
  return hex.match(/.{2}/g).join(':')
}

const play = normalizeFingerprint(process.env.PLAY_APP_SIGNING_SHA256, 'PLAY_APP_SIGNING_SHA256')
const upload = normalizeFingerprint(process.env.UPLOAD_SHA256, 'UPLOAD_SHA256')
const requirePlay = process.env.REQUIRE_PLAY_FINGERPRINT === '1'

if (requirePlay && !play) {
  throw new Error('PLAY_APP_SIGNING_SHA256 is required for a production Google Play assetlinks.json')
}

const fingerprints = [...new Set([play, upload].filter(Boolean))]
if (!fingerprints.length) {
  throw new Error('At least one certificate fingerprint is required')
}

const document = JSON.parse(fs.readFileSync(TEMPLATE, 'utf8'))
const target = document?.[0]?.target
if (!target || target.package_name !== 'com.dontripit.app') {
  throw new Error('Unexpected Digital Asset Links template package')
}

target.sha256_cert_fingerprints = fingerprints
const rendered = `${JSON.stringify(document, null, 2)}\n`

if (rendered.includes('__PLAY_APP_SIGNING_SHA256__') || rendered.includes('__LOCAL_OR_UPLOAD_SHA256__')) {
  throw new Error('Digital Asset Links placeholders were not fully removed')
}

fs.writeFileSync(outputPath, rendered)
console.log(`assetlinks.json written to ${outputPath}`)
console.log(`package: ${target.package_name}`)
console.log(`fingerprints: ${fingerprints.length}`)
