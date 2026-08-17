// Set logos must come from a source that actually exists in the catalog or in
// `public/`. Don’t manufacture `/sets/<game>/<code>.png` candidates: the repo
// currently has no backed set-logo asset tree and guessed paths create visible
// first-party 404s in the public collector UI.
//
// Keep this helper as the single policy boundary so a future canonical asset
// source can opt in deliberately without reintroducing path guessing.
export function getLocalSetImageCandidates() {
  return []
}

export function getPrimaryLocalSetImage() {
  return ''
}
