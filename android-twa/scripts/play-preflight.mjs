import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

const cwd = process.cwd()
const repoRoot = fs.existsSync(path.join(cwd, 'android-twa', 'twa-manifest.json'))
  ? cwd
  : path.resolve(cwd, '..')
const strict = process.argv.includes('--strict')

function file(rel) {
  return path.join(repoRoot, rel)
}

function read(rel) {
  const target = file(rel)
  if (!fs.existsSync(target)) throw new Error(`Missing required file: ${rel}`)
  return fs.readFileSync(target, 'utf8')
}

function json(rel) {
  return JSON.parse(read(rel))
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const requiredFiles = [
  'android-twa/twa-manifest.json',
  'android-twa/.gitignore',
  'android-twa/play/PLAY_CONSOLE_HANDOFF.md',
  'android-twa/play/console-values.json',
  'android-twa/play/release-notes-es-ES.txt',
  'android-twa/play/store-assets-checklist.md',
  'android-twa/fastlane/metadata/android/es-ES/title.txt',
  'android-twa/fastlane/metadata/android/es-ES/short_description.txt',
  'android-twa/fastlane/metadata/android/es-ES/full_description.txt',
  'android-twa/fastlane/metadata/android/es-ES/changelogs/1.txt',
  'frontend/public/manifest.webmanifest',
  'frontend/app/privacy/page.js',
  'frontend/app/delete-account/page.js',
  'frontend/components/dashboard/DashboardPage.js',
  'frontend/app/api/auth/me/route.js',
  'frontend/lib/site.js',
  'backend/app/routes/user_auth.py',
  'docs/android-upload-certificate.md',
  'docs/google-play-data-safety-draft.md',
  'docs/google-play-store-listing.md',
  'docs/google-play-app-content-checklist.md',
  '.github/workflows/android-twa-ci.yml',
  '.github/workflows/android-release.yml',
]
requiredFiles.forEach(read)

const twa = json('android-twa/twa-manifest.json')
assert(twa.packageId === 'com.dontripit.app', `Unexpected packageId: ${twa.packageId}`)
assert(twa.host === 'dontripit.com', `Unexpected TWA host: ${twa.host}`)
assert(twa.name === 'Don’tRipIt', `Unexpected app name: ${twa.name}`)
assert(twa.appVersion === '0.1.0', `Unexpected version name: ${twa.appVersion}`)
assert(Number(twa.appVersionCode) === 1, `Unexpected version code: ${twa.appVersionCode}`)
assert(twa.startUrl === '/?source=android', `Unexpected start URL: ${twa.startUrl}`)
assert(twa.enableNotifications === false, 'Initial wrapper must not request notification integration')
assert(Number(twa.minSdkVersion) === 23, `Unexpected minSdkVersion: ${twa.minSdkVersion}`)
assert(twa.signingKey?.alias === 'dontripit-upload', `Unexpected upload-key alias: ${twa.signingKey?.alias}`)

const ci = read('.github/workflows/android-twa-ci.yml')
assert(ci.includes("compileSdkVersion[[:space:]]+36"), 'Android CI must enforce compileSdkVersion 36')
assert(ci.includes("targetSdkVersion[[:space:]]+36"), 'Android CI must enforce targetSdkVersion 36')

const releaseWorkflow = read('.github/workflows/android-release.yml')
for (const secret of [
  'ANDROID_UPLOAD_KEYSTORE_B64',
  'ANDROID_UPLOAD_KEYSTORE_PASSWORD',
  'ANDROID_UPLOAD_KEY_PASSWORD',
  'ANDROID_UPLOAD_KEY_ALIAS',
]) {
  assert(releaseWorkflow.includes(secret), `Signed release workflow missing protected secret contract: ${secret}`)
}
assert(releaseWorkflow.includes('targetSdkVersion'), 'Signed release workflow must verify target SDK')
assert(releaseWorkflow.includes('app-release-bundle.aab'), 'Signed release workflow must produce an AAB')

const deletionPage = read('frontend/app/delete-account/page.js')
const dashboard = read('frontend/components/dashboard/DashboardPage.js')
const bff = read('frontend/app/api/auth/me/route.js')
const backendAuth = read('backend/app/routes/user_auth.py')
const privacy = read('frontend/app/privacy/page.js')
const siteConfig = read('frontend/lib/site.js')
assert(deletionPage.includes('/dashboard'), 'External deletion page must link to the in-app account controls')
assert(deletionPage.includes('PRIVACY_EMAIL'), 'External deletion page must use the centralized privacy contact')
assert(siteConfig.includes("'info@dontripit.com'"), 'Centralized privacy contact must retain the prepared support address')
assert(dashboard.includes('Eliminar mi cuenta'), 'Dashboard must expose account deletion')
assert(dashboard.includes("toUpperCase() === 'ELIMINAR'"), 'Dashboard must gate destructive confirmation with ELIMINAR')
assert(bff.includes("method: 'DELETE'"), 'Frontend BFF must forward DELETE account requests')
assert(backendAuth.includes('@user_auth_bp.delete("/api/v2/auth/account")'), 'Backend hard-delete route missing')
assert(backendAuth.includes('session.delete(user)'), 'Backend account route must delete the user record')
assert(privacy.includes('/delete-account'), 'Privacy page must reference the external account deletion resource')

const consoleValues = json('android-twa/play/console-values.json')
assert(consoleValues.app.packageId === twa.packageId, 'Play values package does not match TWA package')
assert(consoleValues.app.versionName === twa.appVersion, 'Play values version name does not match TWA manifest')
assert(Number(consoleValues.app.versionCode) === Number(twa.appVersionCode), 'Play values version code does not match TWA manifest')
assert(consoleValues.release.targetSdk === 36, 'Play values must pin target SDK 36')
assert(consoleValues.release.compileSdk === 36, 'Play values must pin compile SDK 36')
assert(consoleValues.release.signedAabSha256 === 'f462851b7356db63c368a30f1f59ae7fc65def55b782f7706b36cc17d93d28f8', 'Approved signed AAB hash changed unexpectedly')
assert(consoleValues.signing.uploadCertificateSha256 === '99:A1:DD:B3:25:FB:1E:7C:A7:03:C4:FD:34:AF:C7:60:0B:E5:66:D2:B3:FF:80:B3:D8:3A:A9:06:47:5D:0A:3F', 'Upload certificate fingerprint changed unexpectedly')

const title = read('android-twa/fastlane/metadata/android/es-ES/title.txt').trim()
const shortDescription = read('android-twa/fastlane/metadata/android/es-ES/short_description.txt').trim()
const fullDescription = read('android-twa/fastlane/metadata/android/es-ES/full_description.txt').trim()
const changelog = read('android-twa/fastlane/metadata/android/es-ES/changelogs/1.txt').trim()
const releaseNotes = read('android-twa/play/release-notes-es-ES.txt').trim()
assert([...title].length <= 30, `Store title exceeds 30 characters: ${[...title].length}`)
assert([...shortDescription].length <= 80, `Short description exceeds 80 characters: ${[...shortDescription].length}`)
assert([...fullDescription].length <= 4000, `Full description exceeds 4000 characters: ${[...fullDescription].length}`)
assert([...changelog].length <= 500, `Changelog exceeds 500 characters: ${[...changelog].length}`)
assert(changelog === releaseNotes, 'Fastlane changelog and prepared release notes must remain identical')
assert(title === consoleValues.app.name, 'Fastlane title and Play values app name differ')
assert(shortDescription === consoleValues.storeListing.shortDescription, 'Fastlane short description and Play values differ')

const androidIgnore = read('android-twa/.gitignore')
for (const pattern of ['*.apk', '*.aab', '*.jks', '*.keystore', 'android.keystore']) {
  assert(androidIgnore.includes(pattern), `android-twa/.gitignore missing secret/output pattern: ${pattern}`)
}

const tracked = execFileSync('git', ['ls-files'], { cwd: repoRoot, encoding: 'utf8' })
  .split(/\r?\n/)
  .filter(Boolean)
const forbiddenTracked = tracked.filter((rel) =>
  /(^|\/)(android\.keystore|[^/]+\.(?:jks|keystore|aab|apk))$/i.test(rel),
)
assert(forbiddenTracked.length === 0, `Signing/binary material must not be tracked: ${forbiddenTracked.join(', ')}`)

const docs = [
  read('docs/google-play-data-safety-draft.md'),
  read('docs/google-play-store-listing.md'),
  read('docs/google-play-app-content-checklist.md'),
]
assert(docs.every((text) => text.includes('com.dontripit.app')), 'Every Play submission draft must identify the package')

const externalBlockers = []
if (consoleValues.signing.playAppSigningCertificateSha256.includes('REQUIRED_')) {
  externalBlockers.push('Play App Signing SHA-256 fingerprint (available only after Play creates/accepts the app signing identity)')
}
if (consoleValues.policy.targetAudience === 'OWNER_DECISION_REQUIRED') {
  externalBlockers.push('Owner target-audience declaration')
}
externalBlockers.push('Dedicated reviewer credentials in the final release environment')
externalBlockers.push('Final 1024×500 feature graphic and real release screenshots')
externalBlockers.push('Paid/verified Google Play developer account and first app creation/upload')

console.log('Google Play technical preflight: PASS')
console.log(`Package: ${twa.packageId}`)
console.log(`Release: ${twa.appVersion} (${twa.appVersionCode})`)
console.log(`Store copy: title ${[...title].length}/30, short ${[...shortDescription].length}/80, full ${[...fullDescription].length}/4000`)
console.log(`Release notes: ${[...releaseNotes].length}/500`)
console.log(`Tracked signing/binary files: ${forbiddenTracked.length}`)
console.log('External gates intentionally not fabricated:')
externalBlockers.forEach((item) => console.log(`- ${item}`))

if (strict && externalBlockers.length) {
  throw new Error(`Strict Play submission mode blocked by ${externalBlockers.length} external gate(s)`)
}
