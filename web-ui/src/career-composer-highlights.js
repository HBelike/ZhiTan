function referenceBoundaryMatches(text, start, tokenLength) {
  const before = start === 0 ? '' : text[start - 1]
  const afterIndex = start + tokenLength
  const after = afterIndex >= text.length ? '' : text[afterIndex]
  return (!before || /\s/.test(before)) && (!after || /\s/.test(after))
}

export function composerReferencePresent(text, token) {
  if (!text || !token) return false
  let start = text.indexOf(token)
  while (start !== -1) {
    if (referenceBoundaryMatches(text, start, token.length)) return true
    start = text.indexOf(token, start + token.length)
  }
  return false
}

function interviewReferenceToken(experience) {
  return `@${experience.company_name}·${experience.role_name}`
}

function selectedReferenceTokens(skills, interviews) {
  const tokens = [
    ...skills.map((skill) => ({ kind: 'skill', text: `/${skill.name}` })),
    ...interviews.map((experience) => ({ kind: 'interview', text: interviewReferenceToken(experience) }))
  ]
  return tokens.filter((token, index) => (
    token.text.length > 1
    && tokens.findIndex((candidate) => candidate.kind === token.kind && candidate.text === token.text) === index
  ))
}

export function buildComposerHighlightSegments(text, skills = [], interviews = []) {
  if (!text) return [{ kind: 'plain', text: '' }]

  const matches = []
  for (const token of selectedReferenceTokens(skills, interviews)) {
    let start = text.indexOf(token.text)
    while (start !== -1) {
      if (referenceBoundaryMatches(text, start, token.text.length)) {
        matches.push({ ...token, start, end: start + token.text.length })
      }
      start = text.indexOf(token.text, start + token.text.length)
    }
  }

  matches.sort((left, right) => left.start - right.start || right.end - left.end)
  const accepted = []
  let occupiedUntil = -1
  for (const match of matches) {
    if (match.start < occupiedUntil) continue
    accepted.push(match)
    occupiedUntil = match.end
  }

  if (!accepted.length) return [{ kind: 'plain', text }]

  const segments = []
  let cursor = 0
  for (const match of accepted) {
    if (match.start > cursor) segments.push({ kind: 'plain', text: text.slice(cursor, match.start) })
    segments.push({ kind: match.kind, text: text.slice(match.start, match.end) })
    cursor = match.end
  }
  if (cursor < text.length) segments.push({ kind: 'plain', text: text.slice(cursor) })
  return segments
}

export function selectedInterviewReferencePresent(text, experience) {
  return composerReferencePresent(text, interviewReferenceToken(experience))
}
